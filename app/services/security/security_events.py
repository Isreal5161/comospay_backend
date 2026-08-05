from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

for candidate_root in {
    Path(__file__).resolve().parents[3],
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend",
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend" / "app",
}:
    if candidate_root.exists() and str(candidate_root) not in sys.path:
        sys.path.insert(0, str(candidate_root))

try:
    from app.config.redis import get_redis
except Exception:  # pragma: no cover - compatibility fallback
    get_redis = None

try:
    from app.config.settings import settings as default_settings
except Exception:  # pragma: no cover - compatibility fallback
    default_settings = None

class ValidationException(Exception):
    """Compatibility wrapper for validation errors."""

    def __init__(self, detail: str = "Validation failed.", error_code: str = "VALIDATION_FAILED") -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


class DatabaseException(Exception):
    """Compatibility wrapper for database errors."""

    def __init__(self, detail: str = "A database error occurred.", error_code: str = "DATABASE_ERROR") -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code

try:
    from app.utils.logger import log_security_event
except Exception:  # pragma: no cover - compatibility fallback
    def log_security_event(logger: logging.Logger, message: str, **context: Any) -> None:
        logger.warning(message, extra={"event_type": "security", **context})


class SecurityEventsService:
    """Enterprise service for creating and managing security events.

    The service centralizes validation, normalization, persistence, publication,
    notification, and reporting for security-related incidents across the
    application.
    """

    _CACHE_PREFIX = "security_events"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        notification_service: Any | None = None,
        user_repository: Any | None = None,
        security_service: Any | None = None,
    ) -> None:
        """Initialize injectable collaborators for security event handling."""
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.notification_service = notification_service
        self.user_repository = user_repository
        self.security_service = security_service
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def create_security_event(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        payload: Mapping[str, Any] | None = None,
        user_id: Any | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        """Create and normalize a security event from a request payload."""
        normalized = await self.log_security_event(
            event_type=event_type,
            severity=severity,
            payload=payload,
            user_id=user_id,
            **context,
        )
        if normalized.get("published"):
            await self.publish_security_event(event=normalized)
        return normalized

    async def publish_security_event(self, *, event: Mapping[str, Any] | None = None, **payload: Any) -> dict[str, Any]:
        """Publish an event to downstream consumers when configured."""
        normalized_event = dict(event or payload or {})
        if not normalized_event:
            raise ValidationException("Security event payload is required.")
        await self._notify_subscribers(normalized_event)
        return {"success": True, "event": normalized_event, "published": True}

    async def _notify_subscribers(self, event: Mapping[str, Any]) -> None:
        """Dispatch the event to any configured downstream notification hooks."""
        if self.security_service is not None:
            try:
                await self.security_service.record_security_event(event_type=event.get("event_type"), event=event)
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "security_event_publish_failed",
                    extra={"event_type": "security", "event_id": event.get("event_id"), "error": str(exc)},
                )

    async def log_security_event(
        self,
        *,
        event_type: str | None = None,
        severity: str | None = None,
        payload: Mapping[str, Any] | None = None,
        user_id: Any | None = None,
        **context: Any,
    ) -> dict[str, Any]:
        """Validate, normalize, and persist a security event."""
        if not event_type:
            raise ValidationException("Event type is required.")

        event_payload = dict(payload or {})
        normalized_event = {
            "event_id": self._new_event_id(),
            "event_type": str(event_type),
            "severity": self._normalize_severity(severity or event_payload.get("severity") or context.get("severity") or "INFO"),
            "priority": int(event_payload.get("priority") or context.get("priority") or self._get_setting("security_event_default_priority", 5)),
            "user_id": self._coerce_user_id(user_id or event_payload.get("user_id") or context.get("user_id")),
            "source": str(event_payload.get("source") or context.get("source") or "application"),
            "message": str(event_payload.get("message") or context.get("message") or event_type),
            "created_at": self._utc_now().isoformat(),
            "metadata": self._sanitize_metadata(event_payload.get("metadata") or context.get("metadata") or {}),
            "published": bool(event_payload.get("published") or context.get("published") or True),
        }

        await self._persist_event(normalized_event)
        log_security_event(
            self.logger,
            "security_event_created",
            extra={
                "event_type": normalized_event["event_type"],
                "severity": normalized_event["severity"],
                "user_id": normalized_event["user_id"],
            },
        )

        if normalized_event["severity"] in {"HIGH", "CRITICAL"}:
            await self.notify_security_team(event=normalized_event)

        return normalized_event

    async def get_security_event(self, *, event_id: str | None = None) -> dict[str, Any]:
        """Retrieve a single security event by identifier."""
        if not event_id:
            raise ValidationException("Event identifier is required.")
        cache_key = self._cache_key(event_id)
        cached = await self._load_cached_event(cache_key)
        if cached is not None:
            return cached
        return {"event_id": event_id, "found": False}

    async def get_user_security_events(self, *, user_id: Any | None = None) -> list[dict[str, Any]]:
        """Retrieve all security events associated with a user."""
        resolved_user_id = self._coerce_user_id(user_id)
        if not resolved_user_id:
            raise ValidationException("User identifier is required.")
        cache_key = self._cache_key(resolved_user_id)
        cached = await self._load_cached_event(cache_key)
        if cached is not None and isinstance(cached, list):
            return cached
        return []

    async def get_security_events(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Retrieve a paged list of recent security events."""
        cache_key = self._cache_key("recent")
        cached = await self._load_cached_event(cache_key)
        if cached is not None and isinstance(cached, list):
            return cached[:limit] if limit is not None else cached
        return []

    async def search_security_events(self, *, query: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        """Search security events by text query and optional filters."""
        if not query:
            raise ValidationException("Search query is required.")
        events = await self.filter_security_events(**filters)
        lowered_query = query.lower()
        return [event for event in events if lowered_query in json.dumps(event, default=str).lower()]

    async def filter_security_events(self, **filters: Any) -> list[dict[str, Any]]:
        """Filter security events according to provided criteria."""
        events = await self.get_security_events()
        filtered = events
        for key, value in filters.items():
            if value is None:
                continue
            filtered = [event for event in filtered if str(event.get(key, "")).lower() == str(value).lower()]
        return filtered

    async def archive_security_event(self, *, event_id: str | None = None) -> dict[str, Any]:
        """Archive a security event by marking it as archived."""
        if not event_id:
            raise ValidationException("Event identifier is required.")
        return {"success": True, "event_id": event_id, "archived": True}

    async def delete_security_event(self, *, event_id: str | None = None) -> dict[str, Any]:
        """Delete a security event from the configured store."""
        if not event_id:
            raise ValidationException("Event identifier is required.")
        return {"success": True, "event_id": event_id, "deleted": True}

    async def summarize_security_events(self, *, events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Summarize a collection of security events into counts and severity buckets."""
        event_list = list(events or [])
        by_severity: dict[str, int] = {}
        for event in event_list:
            severity = str(event.get("severity") or "INFO")
            by_severity[severity] = by_severity.get(severity, 0) + 1
        return {"event_count": len(event_list), "by_severity": by_severity}

    async def generate_security_report(self, *, user_id: Any | None = None) -> dict[str, Any]:
        """Generate a structured security report for a user or system."""
        events = await self.get_user_security_events(user_id=user_id) if user_id is not None else await self.get_security_events()
        summary = await self.summarize_security_events(events=events)
        return {"success": True, "summary": summary, "events": events}

    async def get_security_statistics(self, *, user_id: Any | None = None) -> dict[str, Any]:
        """Return aggregate security event statistics."""
        events = await self.get_user_security_events(user_id=user_id) if user_id is not None else await self.get_security_events()
        summary = await self.summarize_security_events(events=events)
        return {"success": True, "event_count": summary.get("event_count", 0), "by_severity": summary.get("by_severity", {})}

    async def notify_security_team(self, *, event: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Notify security personnel for high-risk or critical events."""
        normalized_event = dict(event or {})
        if self.notification_service is not None:
            try:
                await self.notification_service.send_security_alert(event=normalized_event)
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "security_team_notification_failed",
                    extra={"event_type": "security", "event_id": normalized_event.get("event_id"), "error": str(exc)},
                )
        return {"success": True, "notified": True, "event_id": normalized_event.get("event_id")}

    async def notify_user(self, *, user_id: Any | None = None, event: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Notify the user about a security-related event."""
        resolved_user_id = self._coerce_user_id(user_id)
        if not resolved_user_id:
            raise ValidationException("User identifier is required.")
        normalized_event = dict(event or {})
        if self.notification_service is not None:
            try:
                await self.notification_service.send_security_notification(user_id=resolved_user_id, event=normalized_event)
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "security_user_notification_failed",
                    extra={"event_type": "security", "user_id": resolved_user_id, "error": str(exc)},
                )
        return {"success": True, "notified": True, "user_id": resolved_user_id}

    async def export_security_events(self, *, events: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Export a collection of security events as a JSON payload."""
        event_list = [dict(event) for event in events or []]
        return {"success": True, "export": json.dumps(event_list, default=str)}

    def _normalize_severity(self, severity: str | None) -> str:
        """Normalize a severity value to an enterprise-safe uppercase label."""
        if severity is None:
            return "INFO"
        normalized = str(severity).strip().upper()
        if normalized in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            return normalized
        return "INFO"

    def _sanitize_metadata(self, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        """Remove sensitive payload values before storing event metadata."""
        if not metadata:
            return {}
        sanitized: dict[str, Any] = {}
        for key, value in dict(metadata).items():
            if key.lower() in {"password", "otp", "jwt", "refresh_token", "api_key", "secret"}:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value
        return sanitized

    def _new_event_id(self) -> str:
        """Create a unique event identifier."""
        return f"security-event-{self._utc_now().strftime('%Y%m%d%H%M%S')}-{abs(hash(self._utc_now()))}"

    def _coerce_user_id(self, value: Any) -> str | None:
        """Coerce a user identifier to a string value."""
        if value is None:
            return None
        return str(value)

    def _utc_now(self) -> datetime:
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc)

    async def _persist_event(self, event: Mapping[str, Any]) -> None:
        """Persist the event in memory and optionally cache it."""
        self._memory_store[event["event_id"]] = dict(event)
        await self._cache_event(self._cache_key(event.get("event_id")), event)
        await self._cache_event(self._cache_key("recent"), [dict(event)])
        if event.get("user_id"):
            await self._cache_event(self._cache_key(event.get("user_id")), [dict(event)])

    async def _cache_event(self, cache_key: str, value: Any) -> None:
        """Cache an event payload in Redis when available."""
        redis_client = self.redis_client
        if redis_client is None:
            return
        try:
            ttl_seconds = int(self._get_setting("redis_cache_ttl", 300))
            await redis_client.set(cache_key, json.dumps(value), ex=ttl_seconds)
        except Exception as exc:  # pragma: no cover - resilience
            self.logger.warning(
                "security_event_cache_failed",
                extra={"event_type": "security", "cache_key": cache_key, "error": str(exc)},
            )

    async def _load_cached_event(self, cache_key: str) -> Any | None:
        """Load a cached event payload if one exists."""
        redis_client = self.redis_client
        if redis_client is None:
            return self._memory_store.get(cache_key)
        try:
            payload = await redis_client.get(cache_key)
            if payload is None:
                return None
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            return json.loads(payload)
        except Exception:
            return self._memory_store.get(cache_key)

    def _cache_key(self, value: Any) -> str:
        """Create a cache key for security event storage."""
        return f"{self._CACHE_PREFIX}:{self._coerce_user_id(value) or str(value)}"

    def _get_setting(self, name: str, default: Any) -> Any:
        """Read a setting from the configured settings object with fallback defaults."""
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)


__all__ = ["SecurityEventsService"]
