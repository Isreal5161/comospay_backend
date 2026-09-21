from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.config.redis import get_redis
from app.config.settings import settings
from app.models.device import Device
from app.utils.exceptions import AuthenticationException, ValidationException
from app.utils.logger import get_logger, log_security_event


class SessionNotFoundException(AuthenticationException):
    """Raised when a requested session cannot be located."""

    def __init__(self, detail: str = "Session not found.", error_code: str = "SESSION_NOT_FOUND") -> None:
        super().__init__(detail=detail, error_code=error_code)


class SessionExpiredException(AuthenticationException):
    """Raised when a session has expired and is no longer valid."""

    def __init__(self, detail: str = "Session has expired.", error_code: str = "SESSION_EXPIRED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class SessionRevokedException(AuthenticationException):
    """Raised when a session has been explicitly revoked."""

    def __init__(self, detail: str = "Session has been revoked.", error_code: str = "SESSION_REVOKED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class InvalidSessionException(AuthenticationException):
    """Raised when a session payload is malformed or cannot be used."""

    def __init__(self, detail: str = "Session is invalid.", error_code: str = "INVALID_SESSION") -> None:
        super().__init__(detail=detail, error_code=error_code)


class ConcurrentSessionLimitExceededException(ValidationException):
    """Raised when a user exceeds the configured concurrent session limit."""

    def __init__(self, detail: str = "Concurrent session limit exceeded.", error_code: str = "CONCURRENT_SESSION_LIMIT_EXCEEDED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class SessionService:
    """Service-layer implementation for session lifecycle and security controls."""

    _SESSION_PREFIX = "session"
    _ACTIVE_SESSION_PREFIX = "active_session"
    _SESSION_HISTORY_PREFIX = "session_history"
    _SESSION_BLACKLIST_PREFIX = "session_blacklist"
    _ACTIVE_SESSION_INDEX_PREFIX = "active_sessions"

    def __init__(self, *, redis_client: Any | None = None, logger: logging.Logger | None = None) -> None:
        """Initialize the session service with injectable dependencies."""
        self.redis_client = redis_client
        self.logger = logger or get_logger(__name__)

    async def create_session(
        self,
        *,
        user_id: str,
        device: Device | None = None,
        metadata: Mapping[str, Any] | None = None,
        refresh_token_family_id: str | None = None,
        session_ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Create a new authenticated session record and place it in Redis."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")

        await self._assert_concurrent_session_limit(user_id=user_id)

        session_id = self.generate_session_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=session_ttl_minutes or self._session_timeout_minutes())

        session_payload = {
            "session_id": session_id,
            "user_id": str(user_id),
            "status": "active",
            "created_at": now.isoformat(),
            "last_activity_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "refresh_token_family_id": refresh_token_family_id,
            "device_id": str(device.id) if device and getattr(device, "id", None) is not None else None,
            "device_name": getattr(device, "device_name", None),
            "device_fingerprint": getattr(device, "device_fingerprint", None),
            "browser": None,
            "operating_system": None,
            "ip_address": None,
            "country": None,
            "city": None,
            "user_agent": None,
            "metadata": dict(metadata or {}),
        }

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session storage.")

        await redis_client.set(self._session_key(session_id), json.dumps(session_payload), ex=self._ttl_seconds(expires_at))
        await redis_client.sadd(self._active_session_index_key(user_id), session_id)
        await redis_client.expire(self._active_session_index_key(user_id), self._ttl_seconds(expires_at))
        await self._record_session_history(user_id=user_id, session_id=session_id, action="created")
        log_security_event(self.logger, "Session created", session_id=session_id, user_id=user_id)
        return session_payload

    async def retrieve_session(self, *, session_id: str) -> dict[str, Any]:
        """Retrieve a session payload by identifier."""
        if not session_id:
            raise InvalidSessionException(detail="Session identifier is required.")

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session lookup.")

        payload_raw = await redis_client.get(self._session_key(session_id))
        if not payload_raw:
            raise SessionNotFoundException()

        payload = json.loads(payload_raw)
        await self._assert_session_state(payload)
        return payload

    async def update_session(self, *, session_id: str, **updates: Any) -> dict[str, Any]:
        """Update session metadata while preserving the session lifecycle."""
        payload = await self.retrieve_session(session_id=session_id)
        payload.update({key: value for key, value in updates.items() if value is not None})
        payload["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session update.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def refresh_session(self, *, session_id: str, session_ttl_minutes: int | None = None) -> dict[str, Any]:
        """Refresh the session expiration using sliding expiration semantics."""
        payload = await self.retrieve_session(session_id=session_id)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=session_ttl_minutes or self._session_timeout_minutes())
        payload["expires_at"] = expires_at.isoformat()
        payload["last_activity_at"] = now.isoformat()
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session refresh.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds(expires_at))
        return payload

    async def validate_session(self, *, session_id: str) -> dict[str, Any]:
        """Validate the session and raise clear exceptions when it is invalid."""
        payload = await self.retrieve_session(session_id=session_id)
        if payload.get("status") != "active":
            raise SessionRevokedException()
        if self._is_expired(payload):
            await self.revoke_session(session_id=session_id, reason="expired")
            raise SessionExpiredException()
        return payload

    async def extend_session_expiration(self, *, session_id: str, minutes: int | None = None) -> dict[str, Any]:
        """Extend the absolute expiration for the session."""
        payload = await self.retrieve_session(session_id=session_id)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=minutes or self._session_timeout_minutes())
        payload["expires_at"] = expires_at.isoformat()
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session expiration extension.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds(expires_at))
        return payload

    async def revoke_session(self, *, session_id: str, reason: str | None = None) -> dict[str, Any]:
        """Revoke a specific session and mark it as inactive."""
        payload = await self.retrieve_session(session_id=session_id)
        payload["status"] = "revoked"
        payload["revoked_at"] = datetime.now(timezone.utc).isoformat()
        payload["revoked_reason"] = reason or "manual"
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session revocation.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=60)
        await redis_client.srem(self._active_session_index_key(payload.get("user_id")), session_id)
        await self._record_session_history(user_id=payload.get("user_id"), session_id=session_id, action="revoked")
        log_security_event(self.logger, "Session revoked", session_id=session_id, user_id=payload.get("user_id"), reason=reason)
        return payload

    async def revoke_all_user_sessions(self, *, user_id: str, reason: str | None = None) -> int:
        """Revoke every active session belonging to a user."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session revocation.")
        session_ids = await redis_client.smembers(self._active_session_index_key(user_id))
        for session_id in session_ids:
            await self.revoke_session(session_id=session_id, reason=reason or "user_revoke")
        return len(session_ids)

    async def revoke_all_sessions_except_current(self, *, user_id: str, current_session_id: str, reason: str | None = None) -> int:
        """Revoke all sessions for a user except the current one."""
        if not user_id or not current_session_id:
            raise InvalidSessionException(detail="User and current session identifiers are required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session revocation.")
        session_ids = await redis_client.smembers(self._active_session_index_key(user_id))
        revoked = 0
        for session_id in session_ids:
            if session_id == current_session_id:
                continue
            await self.revoke_session(session_id=session_id, reason=reason or "other_session_revoke")
            revoked += 1
        return revoked

    async def check_session_status(self, *, session_id: str) -> str:
        """Return the current state of a session."""
        try:
            payload = await self.retrieve_session(session_id=session_id)
        except (SessionNotFoundException, SessionExpiredException, SessionRevokedException):
            return "inactive"
        return str(payload.get("status") or "inactive")

    async def check_session_expiration(self, *, session_id: str) -> bool:
        """Return True when the session has expired."""
        try:
            payload = await self.retrieve_session(session_id=session_id)
        except SessionExpiredException:
            return True
        except (SessionNotFoundException, SessionRevokedException):
            return True
        return self._is_expired(payload)

    async def get_active_sessions(self, *, user_id: str) -> list[dict[str, Any]]:
        """Return all active sessions for the supplied user."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session lookup.")
        session_ids = await redis_client.smembers(self._active_session_index_key(user_id))
        sessions: list[dict[str, Any]] = []
        for session_id in session_ids:
            try:
                payload = await self.retrieve_session(session_id=session_id)
            except (SessionNotFoundException, SessionExpiredException, SessionRevokedException):
                continue
            sessions.append(payload)
        return sessions

    async def get_session_history(self, *, user_id: str) -> list[dict[str, Any]]:
        """Return session history entries for the supplied user."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session history lookup.")
        history_raw = await redis_client.lrange(self._session_history_key(user_id), 0, -1)
        return [json.loads(item) for item in history_raw if item]

    async def clean_expired_sessions(self) -> int:
        """Remove expired session records from Redis and return the count removed."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return 0
        removed = 0
        async for key in redis_client.scan_iter(match=f"{self._SESSION_PREFIX}:*"):
            payload_raw = await redis_client.get(key)
            if not payload_raw:
                continue
            payload = json.loads(payload_raw)
            if self._is_expired(payload):
                await redis_client.delete(key)
                removed += 1
        return removed

    async def associate_session_with_refresh_token(self, *, session_id: str, refresh_token_family_id: str) -> dict[str, Any]:
        """Attach a refresh-token family identifier to a session."""
        payload = await self.retrieve_session(session_id=session_id)
        payload["refresh_token_family_id"] = refresh_token_family_id
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session association.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def associate_session_with_device(self, *, session_id: str, device: Device | None) -> dict[str, Any]:
        """Attach the supplied device metadata to an existing session."""
        payload = await self.retrieve_session(session_id=session_id)
        if device is not None:
            payload["device_id"] = str(device.id) if getattr(device, "id", None) is not None else None
            payload["device_name"] = getattr(device, "device_name", None)
            payload["device_fingerprint"] = getattr(device, "device_fingerprint", None)
            payload["browser"] = getattr(device, "browser_name", None)
            payload["operating_system"] = getattr(device, "operating_system", None)
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session association.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def record_session_activity(self, *, session_id: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Record activity for a session and refresh its last activity timestamp."""
        payload = await self.retrieve_session(session_id=session_id)
        payload["last_activity_at"] = datetime.now(timezone.utc).isoformat()
        if metadata:
            payload.setdefault("metadata", {}).update(dict(metadata))
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for session activity recording.")
        await redis_client.set(self._session_key(session_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def update_last_activity_timestamp(self, *, session_id: str) -> dict[str, Any]:
        """Update the last activity timestamp for a session without changing other metadata."""
        return await self.record_session_activity(session_id=session_id)

    def generate_session_id(self) -> str:
        """Generate a cryptographically secure session identifier."""
        return secrets.token_urlsafe(24)

    async def _record_session_history(self, *, user_id: str, session_id: str, action: str) -> None:
        """Store a session history entry in Redis for auditing and debugging."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return
        entry = {"session_id": session_id, "action": action, "timestamp": datetime.now(timezone.utc).isoformat()}
        await redis_client.lpush(self._session_history_key(user_id), json.dumps(entry))
        await redis_client.ltrim(self._session_history_key(user_id), 0, 49)

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client when available, otherwise None."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("Redis unavailable for session operations: %s", exc)
            return None

    async def _assert_session_state(self, payload: Mapping[str, Any]) -> None:
        """Validate the session status and raise clear errors when state is invalid."""
        status = str(payload.get("status") or "")
        if status == "revoked":
            raise SessionRevokedException()
        if self._is_expired(payload):
            raise SessionExpiredException()

    async def _assert_concurrent_session_limit(self, *, user_id: str) -> None:
        """Enforce the configured concurrent-session limit before creating a new session."""
        max_sessions = self._concurrent_session_limit()
        if max_sessions <= 0:
            return
        try:
            active_count = len(await self._active_session_ids(user_id))
        except Exception:
            active_count = 0
        if active_count >= max_sessions:
            raise ConcurrentSessionLimitExceededException()

    async def _active_session_ids(self, user_id: str) -> list[str]:
        """Return active session identifiers for a user."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return []
        session_ids = await redis_client.smembers(self._active_session_index_key(user_id))
        return [str(session_id) for session_id in session_ids if session_id]

    def _session_key(self, session_id: str) -> str:
        """Build a Redis key for a session payload."""
        return f"{self._SESSION_PREFIX}:{session_id}"

    def _active_session_index_key(self, user_id: str) -> str:
        """Build a Redis key for a user's active session index."""
        return f"{self._ACTIVE_SESSION_INDEX_PREFIX}:{user_id}"

    def _session_history_key(self, user_id: str) -> str:
        """Build a Redis key for a user's session history."""
        return f"{self._SESSION_HISTORY_PREFIX}:{user_id}"

    def _session_timeout_minutes(self) -> int:
        """Read the configured session timeout from settings or environment variables."""
        return self._read_int_value("SESSION_TIMEOUT_MINUTES", default=getattr(settings, "session_timeout_minutes", 60))

    def _concurrent_session_limit(self) -> int:
        """Read the configured concurrent session limit from settings or environment variables."""
        return self._read_int_value("CONCURRENT_SESSION_LIMIT", default=getattr(settings, "concurrent_session_limit", 5))

    def _read_int_value(self, name: str, *, default: int) -> int:
        """Read an integer configuration value from settings or environment variables."""
        if hasattr(settings, name.lower()):
            value = getattr(settings, name.lower(), None)
            if isinstance(value, int):
                return value
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return int(raw_value)
        except ValueError:
            return default

    def _ttl_seconds(self, expires_at: datetime) -> int:
        """Compute the Redis TTL in seconds from an expiration timestamp."""
        return max(int((expires_at - datetime.now(timezone.utc)).total_seconds()), 60)

    def _ttl_seconds_from_payload(self, payload: Mapping[str, Any]) -> int:
        """Compute the remaining TTL from a session payload."""
        expires_at = payload.get("expires_at")
        if not expires_at:
            return 60
        try:
            expiry_dt = datetime.fromisoformat(str(expires_at))
        except ValueError:
            return 60
        return max(int((expiry_dt - datetime.now(timezone.utc)).total_seconds()), 60)

    def _is_expired(self, payload: Mapping[str, Any]) -> bool:
        """Return True when the session has exceeded its expiration timestamp."""
        expires_at = payload.get("expires_at")
        if not expires_at:
            return True
        try:
            expiry_dt = datetime.fromisoformat(str(expires_at))
        except ValueError:
            return True
        return datetime.now(timezone.utc) >= expiry_dt


__all__ = [
    "SessionService",
    "SessionNotFoundException",
    "SessionExpiredException",
    "SessionRevokedException",
    "InvalidSessionException",
    "ConcurrentSessionLimitExceededException",
]
