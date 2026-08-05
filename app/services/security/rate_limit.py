from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

for candidate_root in {
    Path(__file__).resolve().parents[3],
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend",
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend" / "app",
}:
    if candidate_root.exists() and str(candidate_root) not in sys.path:
        sys.path.insert(0, str(candidate_root))

try:
    from app.config.settings import settings as default_settings
except ImportError:  # pragma: no cover - compatibility fallback
    default_settings = None

try:
    from app.utils.exceptions import ValidationException
except ImportError:  # pragma: no cover - compatibility fallback
    ValidationException = Any  # type: ignore[misc,assignment]


class RateLimitService:
    """Enterprise rate limiting service for throttling API traffic.

    The service is intentionally limited to rate-limit evaluation and request
    accounting. It uses Redis-backed counters when available and can fall back
    to an in-memory store for local execution.
    """

    _KEY_PREFIX = "ratelimit"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def check_rate_limit(
        self,
        *,
        key: str | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        ttl_seconds: int | None = None,
        algorithm: str | None = None,
        request_context: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Evaluate whether a request should be allowed under the configured policy."""
        if not key:
            raise ValidationException("Rate-limit key is required.")

        request_context = request_context or {}
        effective_limit = self._resolve_limit(limit=limit, request_context=request_context)
        effective_window = self._resolve_window(window_seconds=window_seconds, request_context=request_context)
        effective_ttl = self._resolve_ttl(ttl_seconds=ttl_seconds, request_context=request_context)
        effective_algorithm = (algorithm or self._resolve_algorithm(request_context=request_context) or "fixed_window").lower()

        entries = await self._load_state(key)
        now = self._utc_now()
        window_start = self._window_start(now, effective_window)

        if effective_algorithm == "sliding_window":
            state = self._build_sliding_window_state(entries, now, effective_window)
            count = int(state.get("count", 0))
            allowed = count < effective_limit
            if allowed:
                state["count"] = count + 1
                state["updated_at"] = now.isoformat()
                state["window_start"] = window_start.isoformat()
                state["window_end"] = (now + timedelta(seconds=effective_window)).isoformat()
                state.setdefault("created_at", now.isoformat())
                await self._save_state(key, state, ttl_seconds=effective_ttl)
            else:
                state.setdefault("count", count)
                state["updated_at"] = now.isoformat()
                state["window_start"] = window_start.isoformat()
                await self._save_state(key, state, ttl_seconds=effective_ttl)
        elif effective_algorithm == "sliding_log":
            state = self._build_sliding_log_state(entries, now, effective_window)
            log_entries = state.setdefault("history", [])
            if not isinstance(log_entries, list):
                log_entries = []
            log_entries.append(now.isoformat())
            state["history"] = [entry for entry in log_entries if self._parse_timestamp(entry) and (now - self._parse_timestamp(entry)).total_seconds() <= effective_window]
            remaining = effective_limit - len(state["history"])
            allowed = remaining > 0
            state["count"] = len(state["history"])
            state["updated_at"] = now.isoformat()
            await self._save_state(key, state, ttl_seconds=effective_ttl)
        elif effective_algorithm == "token_bucket":
            state = self._build_token_bucket_state(entries, now)
            capacity = max(1, effective_limit)
            refill_rate = capacity / max(1, effective_window)
            current_tokens = float(state.get("tokens", capacity))
            if current_tokens < 1:
                allowed = False
            else:
                current_tokens = max(0.0, current_tokens - 1)
                allowed = True
            if allowed:
                state["tokens"] = current_tokens
            else:
                state["tokens"] = current_tokens
            state["updated_at"] = now.isoformat()
            state["refill_rate"] = refill_rate
            await self._save_state(key, state, ttl_seconds=effective_ttl)
        else:
            state = self._build_fixed_window_state(entries, now, effective_window)
            count = int(state.get("count", 0))
            allowed = count < effective_limit
            if allowed:
                state["count"] = count + 1
                state["updated_at"] = now.isoformat()
                state["window_start"] = window_start.isoformat()
                await self._save_state(key, state, ttl_seconds=effective_ttl)
            else:
                state["updated_at"] = now.isoformat()
                await self._save_state(key, state, ttl_seconds=effective_ttl)

        remaining_requests = max(0, effective_limit - self._get_count_from_state(state))
        retry_after = self._calculate_retry_after(state=state, now=now, effective_window=effective_window, effective_limit=effective_limit)
        limited = not allowed
        return {
            "success": True,
            "allowed": allowed,
            "limited": limited,
            "key": key,
            "limit": effective_limit,
            "remaining_requests": remaining_requests,
            "retry_after": retry_after,
            "window_seconds": effective_window,
            "algorithm": effective_algorithm,
            "request_context": request_context,
        }

    async def increment_request_count(
        self,
        *,
        key: str | None = None,
        limit: int | None = None,
        window_seconds: int | None = None,
        ttl_seconds: int | None = None,
        algorithm: str | None = None,
        request_context: dict[str, Any] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Increment the request count for the supplied rate-limit key."""
        return await self.check_rate_limit(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            ttl_seconds=ttl_seconds,
            algorithm=algorithm,
            request_context=request_context,
            **payload,
        )

    async def reset_request_count(self, *, key: str | None = None, **payload: Any) -> dict[str, Any]:
        """Reset the rate-limit state for a key."""
        if not key:
            raise ValidationException("Rate-limit key is required.")
        await self._delete_state(key)
        return {"success": True, "reset": True, "key": key}

    async def get_remaining_requests(self, *, key: str | None = None, **payload: Any) -> dict[str, Any]:
        """Return the remaining request count under the active rate-limit policy."""
        if not key:
            raise ValidationException("Rate-limit key is required.")
        state = await self._load_state(key)
        limit = self._resolve_limit(limit=None, request_context=payload.get("request_context") or {})
        remaining = max(0, limit - self._get_count_from_state(state))
        return {"success": True, "key": key, "remaining_requests": remaining, "limit": limit}

    async def get_retry_after(self, *, key: str | None = None, **payload: Any) -> dict[str, Any]:
        """Return the retry-after interval for the supplied key."""
        if not key:
            raise ValidationException("Rate-limit key is required.")
        state = await self._load_state(key)
        now = self._utc_now()
        window_seconds = self._resolve_window(window_seconds=None, request_context=payload.get("request_context") or {})
        limit = self._resolve_limit(limit=None, request_context=payload.get("request_context") or {})
        retry_after = self._calculate_retry_after(state=state, now=now, effective_window=window_seconds, effective_limit=limit)
        return {"success": True, "key": key, "retry_after": retry_after}

    async def is_rate_limited(self, *, key: str | None = None, **payload: Any) -> bool:
        """Return True when the supplied key is currently rate limited."""
        if not key:
            raise ValidationException("Rate-limit key is required.")
        state = await self._load_state(key)
        limit = self._resolve_limit(limit=None, request_context=payload.get("request_context") or {})
        return self._get_count_from_state(state) >= limit

    async def clear_expired_limits(self, *, key: str | None = None, **payload: Any) -> dict[str, Any]:
        """Clear expired or stale rate-limit entries."""
        if key:
            await self._delete_state(key)
        else:
            self._memory_store.clear()
        return {"success": True, "cleared": True, "key": key}

    async def get_rate_limit_statistics(self, *, key: str | None = None, **payload: Any) -> dict[str, Any]:
        """Return a structured rate-limit activity snapshot."""
        if key:
            state = await self._load_state(key)
            return {"success": True, "key": key, "count": self._get_count_from_state(state), "state": state}
        return {"success": True, "keys": list(self._memory_store.keys()), "count": len(self._memory_store)}

    async def _load_state(self, key: str) -> dict[str, Any]:
        if self.redis_client is not None:
            try:
                payload = await self.redis_client.get(key)
                if payload:
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    state = json.loads(payload)
                    if isinstance(state, dict):
                        return state
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "rate_limit_redis_load_failed",
                    extra={"event_type": "security", "component": "RateLimitService", "error": str(exc)},
                )
        return dict(self._memory_store.get(key, {}))

    async def _save_state(self, key: str, state: dict[str, Any], *, ttl_seconds: int) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.set(key, json.dumps(state), ex=max(60, ttl_seconds))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "rate_limit_redis_save_failed",
                    extra={"event_type": "security", "component": "RateLimitService", "error": str(exc)},
                )
        self._memory_store[key] = dict(state)

    async def _delete_state(self, key: str) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.delete(key)
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "rate_limit_redis_delete_failed",
                    extra={"event_type": "security", "component": "RateLimitService", "error": str(exc)},
                )
        self._memory_store.pop(key, None)

    def _resolve_limit(self, *, limit: int | None, request_context: dict[str, Any]) -> int:
        if limit is not None:
            return int(limit)
        policy_name = request_context.get("policy_name") or request_context.get("endpoint") or request_context.get("route")
        if policy_name:
            configured = self._get_setting(f"rate_limit_{policy_name}_limit")
            if configured is not None:
                return int(configured)
        configured = self._get_setting("rate_limit_default_limit", 60)
        return int(configured)

    def _resolve_window(self, *, window_seconds: int | None, request_context: dict[str, Any]) -> int:
        if window_seconds is not None:
            return int(window_seconds)
        policy_name = request_context.get("policy_name") or request_context.get("endpoint") or request_context.get("route")
        if policy_name:
            configured = self._get_setting(f"rate_limit_{policy_name}_window_seconds")
            if configured is not None:
                return int(configured)
        configured = self._get_setting("rate_limit_default_window_seconds", 60)
        return int(configured)

    def _resolve_ttl(self, *, ttl_seconds: int | None, request_context: dict[str, Any]) -> int:
        if ttl_seconds is not None:
            return int(ttl_seconds)
        configured = self._get_setting("rate_limit_default_ttl_seconds", self._resolve_window(window_seconds=None, request_context=request_context) + 60)
        return int(configured)

    def _resolve_algorithm(self, *, request_context: dict[str, Any]) -> str | None:
        policy_name = request_context.get("policy_name") or request_context.get("endpoint") or request_context.get("route")
        if policy_name:
            configured = self._get_setting(f"rate_limit_{policy_name}_algorithm")
            if configured is not None:
                return str(configured)
        configured = self._get_setting("rate_limit_default_algorithm", "fixed_window")
        return str(configured)

    def _get_setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _window_start(self, now: datetime, window_seconds: int) -> datetime:
        epoch = now.replace(minute=0, second=0, microsecond=0)
        return epoch

    def _build_fixed_window_state(self, entries: dict[str, Any], now: datetime, window_seconds: int) -> dict[str, Any]:
        state = dict(entries)
        state.setdefault("count", 0)
        state.setdefault("first_seen_at", now.isoformat())
        state.setdefault("last_seen_at", now.isoformat())
        return state

    def _build_sliding_window_state(self, entries: dict[str, Any], now: datetime, window_seconds: int) -> dict[str, Any]:
        state = dict(entries)
        history = state.setdefault("history", [])
        if not isinstance(history, list):
            history = []
        state["history"] = [entry for entry in history if self._parse_timestamp(entry) and (now - self._parse_timestamp(entry)).total_seconds() <= window_seconds]
        state["count"] = len(state["history"])
        state.setdefault("first_seen_at", now.isoformat())
        state.setdefault("last_seen_at", now.isoformat())
        state["updated_at"] = now.isoformat()
        return state

    def _build_sliding_log_state(self, entries: dict[str, Any], now: datetime, window_seconds: int) -> dict[str, Any]:
        state = dict(entries)
        history = state.setdefault("history", [])
        if not isinstance(history, list):
            history = []
        state["history"] = [entry for entry in history if self._parse_timestamp(entry) and (now - self._parse_timestamp(entry)).total_seconds() <= window_seconds]
        state.setdefault("first_seen_at", now.isoformat())
        state.setdefault("last_seen_at", now.isoformat())
        return state

    def _build_token_bucket_state(self, entries: dict[str, Any], now: datetime) -> dict[str, Any]:
        state = dict(entries)
        state.setdefault("tokens", self._resolve_limit(limit=None, request_context={}) or 1)
        state.setdefault("last_refill_at", now.isoformat())
        return state

    def _calculate_retry_after(self, *, state: dict[str, Any], now: datetime, effective_window: int, effective_limit: int) -> int:
        count = self._get_count_from_state(state)
        if count < effective_limit:
            return 0
        return max(1, effective_window)

    def _get_count_from_state(self, state: dict[str, Any]) -> int:
        if isinstance(state.get("history"), list):
            return len(state["history"])
        value = state.get("count")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["RateLimitService"]
