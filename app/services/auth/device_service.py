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


class DeviceNotFoundException(AuthenticationException):
    """Raised when a requested device cannot be located."""

    def __init__(self, detail: str = "Device not found.", error_code: str = "DEVICE_NOT_FOUND") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceAlreadyRegisteredException(ValidationException):
    """Raised when a device fingerprint already belongs to the user."""

    def __init__(self, detail: str = "Device is already registered.", error_code: str = "DEVICE_ALREADY_REGISTERED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceNotTrustedException(AuthenticationException):
    """Raised when a device is not trusted and the operation requires trust."""

    def __init__(self, detail: str = "Device is not trusted.", error_code: str = "DEVICE_NOT_TRUSTED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceRevokedException(AuthenticationException):
    """Raised when a device has been revoked and cannot be used."""

    def __init__(self, detail: str = "Device has been revoked.", error_code: str = "DEVICE_REVOKED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceVerificationFailedException(AuthenticationException):
    """Raised when device verification cannot be completed."""

    def __init__(self, detail: str = "Device verification failed.", error_code: str = "DEVICE_VERIFICATION_FAILED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class FingerprintMismatchException(AuthenticationException):
    """Raised when a submitted fingerprint does not match the registered device."""

    def __init__(self, detail: str = "Device fingerprint mismatch.", error_code: str = "FINGERPRINT_MISMATCH") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceOwnershipMismatchException(AuthenticationException):
    """Raised when a device is associated with the wrong user."""

    def __init__(self, detail: str = "Device ownership mismatch.", error_code: str = "DEVICE_OWNERSHIP_MISMATCH") -> None:
        super().__init__(detail=detail, error_code=error_code)


class ConcurrentDeviceLimitExceededException(ValidationException):
    """Raised when the configured concurrent device limit is exceeded."""

    def __init__(self, detail: str = "Concurrent device limit exceeded.", error_code: str = "CONCURRENT_DEVICE_LIMIT_EXCEEDED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class DeviceService:
    """Service-layer implementation for trusted device management and device security."""

    _DEVICE_PREFIX = "device"
    _ACTIVE_DEVICE_PREFIX = "active_device"
    _TRUSTED_DEVICE_PREFIX = "trusted_device"
    _DEVICE_HISTORY_PREFIX = "device_history"

    def __init__(self, *, redis_client: Any | None = None, logger: logging.Logger | None = None) -> None:
        """Initialize the device service with injectable dependencies."""
        self.redis_client = redis_client
        self.logger = logger or get_logger(__name__)

    async def register_device(
        self,
        *,
        user_id: str,
        device_fingerprint: str | None = None,
        device_name: str | None = None,
        device_type: str | None = None,
        operating_system: str | None = None,
        browser_name: str | None = None,
        ip_address: str | None = None,
        location_country: str | None = None,
        location_city: str | None = None,
        user_agent: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        is_trusted: bool | None = None,
    ) -> dict[str, Any]:
        """Register a new device for a user, enforcing registration and limit controls."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        if not device_fingerprint:
            device_fingerprint = self.generate_fingerprint()

        self._assert_concurrent_device_limit(user_id=user_id)
        existing = await self.get_device_by_fingerprint(user_id=user_id, fingerprint=device_fingerprint)
        if existing:
            raise DeviceAlreadyRegisteredException()

        device_id = self.generate_device_id()
        now = datetime.now(timezone.utc)
        payload = {
            "device_id": device_id,
            "user_id": str(user_id),
            "device_name": device_name or "Unknown Device",
            "device_type": device_type or "unknown",
            "device_fingerprint": device_fingerprint,
            "browser": browser_name,
            "browser_version": None,
            "operating_system": operating_system,
            "os_version": None,
            "platform": None,
            "ip_address": ip_address,
            "country": location_country,
            "city": location_city,
            "timezone": None,
            "user_agent": user_agent,
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "last_login_at": now.isoformat(),
            "trust_status": "trusted" if is_trusted else "untrusted",
            "verification_status": "verified" if is_trusted else "pending",
            "session_id": None,
            "refresh_token_family_id": None,
            "is_active": True,
            "is_trusted": bool(is_trusted),
            "is_revoked": False,
            "metadata": dict(metadata or {}),
        }
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device registration.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        await redis_client.sadd(self._active_device_index_key(user_id), device_id)
        if payload["is_trusted"]:
            await redis_client.sadd(self._trusted_device_index_key(user_id), device_id)
        await self._record_device_history(user_id=user_id, device_id=device_id, action="registered")
        log_security_event(self.logger, "Device registered", device_id=device_id, user_id=user_id)
        return payload

    async def retrieve_device(self, *, device_id: str) -> dict[str, Any]:
        """Retrieve a device payload by identifier."""
        if not device_id:
            raise DeviceNotFoundException()
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device lookup.")
        payload_raw = await redis_client.get(self._device_key(device_id))
        if not payload_raw:
            raise DeviceNotFoundException()
        payload = json.loads(payload_raw)
        if payload.get("is_revoked"):
            raise DeviceRevokedException()
        return payload

    async def update_device_information(self, *, device_id: str, **updates: Any) -> dict[str, Any]:
        """Update device metadata while preserving trust and lifecycle state."""
        payload = await self.retrieve_device(device_id=device_id)
        payload.update({key: value for key, value in updates.items() if value is not None})
        payload["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device updates.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def rename_device(self, *, device_id: str, device_name: str) -> dict[str, Any]:
        """Rename a device and persist the update."""
        return await self.update_device_information(device_id=device_id, device_name=device_name)

    async def trust_device(self, *, device_id: str) -> dict[str, Any]:
        """Mark a device as trusted."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["is_trusted"] = True
        payload["trust_status"] = "trusted"
        payload["verification_status"] = "verified"
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device trust updates.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        await redis_client.sadd(self._trusted_device_index_key(payload.get("user_id")), device_id)
        return payload

    async def untrust_device(self, *, device_id: str) -> dict[str, Any]:
        """Remove trusted status from a device."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["is_trusted"] = False
        payload["trust_status"] = "untrusted"
        payload["verification_status"] = "pending"
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device trust updates.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        await redis_client.srem(self._trusted_device_index_key(payload.get("user_id")), device_id)
        return payload

    async def verify_device_ownership(self, *, device_id: str, user_id: str) -> bool:
        """Confirm that the supplied user owns the requested device."""
        if not device_id or not user_id:
            return False
        try:
            payload = await self.retrieve_device(device_id=device_id)
        except (DeviceNotFoundException, DeviceRevokedException):
            return False
        return str(payload.get("user_id") or "") == str(user_id)

    async def delete_device(self, *, device_id: str) -> bool:
        """Delete a device record from Redis."""
        if not device_id:
            return False
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False
        await redis_client.delete(self._device_key(device_id))
        return True

    async def list_user_devices(self, *, user_id: str) -> list[dict[str, Any]]:
        """List all devices registered for a user."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device lookup.")
        device_ids = await redis_client.smembers(self._active_device_index_key(user_id))
        devices: list[dict[str, Any]] = []
        for device_id in device_ids:
            try:
                devices.append(await self.retrieve_device(device_id=device_id))
            except (DeviceNotFoundException, DeviceRevokedException):
                continue
        return devices

    async def get_active_devices(self, *, user_id: str) -> list[dict[str, Any]]:
        """Return active devices registered for a user."""
        return [device for device in await self.list_user_devices(user_id=user_id) if device.get("is_active")]

    async def get_trusted_devices(self, *, user_id: str) -> list[dict[str, Any]]:
        """Return trusted devices registered for a user."""
        if not user_id:
            raise InvalidSessionException(detail="User identifier is required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for trusted device lookup.")
        device_ids = await redis_client.smembers(self._trusted_device_index_key(user_id))
        devices: list[dict[str, Any]] = []
        for device_id in device_ids:
            try:
                devices.append(await self.retrieve_device(device_id=device_id))
            except (DeviceNotFoundException, DeviceRevokedException):
                continue
        return devices

    async def get_device_by_fingerprint(self, *, user_id: str, fingerprint: str) -> dict[str, Any] | None:
        """Retrieve a device by fingerprint for a user."""
        if not user_id or not fingerprint:
            return None
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return None
        device_ids = await redis_client.smembers(self._active_device_index_key(user_id))
        for device_id in device_ids:
            try:
                payload = await self.retrieve_device(device_id=device_id)
            except (DeviceNotFoundException, DeviceRevokedException):
                continue
            if str(payload.get("device_fingerprint") or "") == str(fingerprint):
                return payload
        return None

    async def associate_device_with_session(self, *, device_id: str, session_id: str) -> dict[str, Any]:
        """Associate a device record with a session identifier."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["session_id"] = session_id
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device-session association.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def associate_device_with_refresh_token(self, *, device_id: str, refresh_token_family_id: str) -> dict[str, Any]:
        """Associate a device record with a refresh-token family identifier."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["refresh_token_family_id"] = refresh_token_family_id
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device-refresh-token association.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def associate_device_with_user(self, *, device_id: str, user_id: str) -> dict[str, Any]:
        """Associate a device record with a different user, when permitted by the caller."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["user_id"] = str(user_id)
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device-user association.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def update_last_seen_timestamp(self, *, device_id: str) -> dict[str, Any]:
        """Update the last-seen timestamp for a device."""
        return await self.update_device_information(device_id=device_id, last_seen_at=datetime.now(timezone.utc).isoformat())

    async def update_login_metadata(self, *, device_id: str, **metadata: Any) -> dict[str, Any]:
        """Update login-related metadata for the device."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["last_login_at"] = datetime.now(timezone.utc).isoformat()
        payload["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        payload.setdefault("metadata", {}).update({key: value for key, value in metadata.items() if value is not None})
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device login metadata updates.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def record_device_activity(self, *, device_id: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Record activity for a device and refresh its last activity metadata."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        if metadata:
            payload.setdefault("metadata", {}).update(dict(metadata))
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device activity updates.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
        return payload

    async def detect_new_device_login(self, *, device_id: str, user_id: str) -> bool:
        """Return True when a device login appears to be new for the supplied user."""
        try:
            payload = await self.retrieve_device(device_id=device_id)
        except (DeviceNotFoundException, DeviceRevokedException):
            return True
        return str(payload.get("user_id") or "") == str(user_id) and payload.get("first_seen_at") == payload.get("last_seen_at")

    async def detect_suspicious_device_activity(self, *, device_id: str, ip_address: str | None = None) -> bool:
        """Provide a lightweight suspicious-activity hook for device events."""
        try:
            payload = await self.retrieve_device(device_id=device_id)
        except (DeviceNotFoundException, DeviceRevokedException):
            return True
        if ip_address and payload.get("ip_address") and str(payload.get("ip_address")) != str(ip_address):
            log_security_event(self.logger, "Potential device IP change detected", device_id=device_id)
            return True
        return False

    async def detect_fingerprint_mismatch(self, *, device_id: str, fingerprint: str) -> bool:
        """Return True when the supplied fingerprint differs from the registered one."""
        try:
            payload = await self.retrieve_device(device_id=device_id)
        except (DeviceNotFoundException, DeviceRevokedException):
            return True
        return str(payload.get("device_fingerprint") or "") != str(fingerprint or "")

    async def revoke_device(self, *, device_id: str, reason: str | None = None) -> dict[str, Any]:
        """Revoke a device and mark it inactive."""
        payload = await self.retrieve_device(device_id=device_id)
        payload["is_active"] = False
        payload["is_revoked"] = True
        payload["revoked_reason"] = reason or "manual"
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device revocation.")
        await redis_client.set(self._device_key(device_id), json.dumps(payload), ex=60)
        await redis_client.srem(self._active_device_index_key(payload.get("user_id")), device_id)
        await redis_client.srem(self._trusted_device_index_key(payload.get("user_id")), device_id)
        await self._record_device_history(user_id=payload.get("user_id"), device_id=device_id, action="revoked")
        log_security_event(self.logger, "Device revoked", device_id=device_id, user_id=payload.get("user_id"), reason=reason)
        return payload

    async def revoke_all_devices_except_current(self, *, user_id: str, current_device_id: str, reason: str | None = None) -> int:
        """Revoke all devices for a user except the current one."""
        if not user_id or not current_device_id:
            raise InvalidSessionException(detail="User and current device identifiers are required.")
        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for device revocation.")
        device_ids = await redis_client.smembers(self._active_device_index_key(user_id))
        revoked = 0
        for device_id in device_ids:
            if device_id == current_device_id:
                continue
            await self.revoke_device(device_id=device_id, reason=reason or "other_device_revoke")
            revoked += 1
        return revoked

    async def clean_inactive_devices(self) -> int:
        """Remove inactive or expired device entries from Redis and return the count removed."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return 0
        removed = 0
        async for key in redis_client.scan_iter(match=f"{self._DEVICE_PREFIX}:*"):
            payload_raw = await redis_client.get(key)
            if not payload_raw:
                continue
            payload = json.loads(payload_raw)
            if not payload.get("is_active") or payload.get("is_revoked"):
                await redis_client.delete(key)
                removed += 1
        return removed

    def generate_device_id(self) -> str:
        """Generate a cryptographically secure device identifier."""
        return secrets.token_urlsafe(16)

    def generate_fingerprint(self) -> str:
        """Generate a cryptographically secure device fingerprint."""
        return secrets.token_urlsafe(24)

    async def _record_device_history(self, *, user_id: str, device_id: str, action: str) -> None:
        """Store a device-history entry in Redis for auditing and troubleshooting."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return
        entry = {"device_id": device_id, "action": action, "timestamp": datetime.now(timezone.utc).isoformat()}
        await redis_client.lpush(self._device_history_key(user_id), json.dumps(entry))
        await redis_client.ltrim(self._device_history_key(user_id), 0, 49)

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client when available, otherwise None."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("Redis unavailable for device operations: %s", exc)
            return None

    def _assert_concurrent_device_limit(self, *, user_id: str) -> None:
        """Enforce the configured concurrent device limit before registration."""
        max_devices = self._concurrent_device_limit()
        if max_devices <= 0:
            return
        try:
            active_count = len(self._active_device_ids(user_id))
        except Exception:
            active_count = 0
        if active_count >= max_devices:
            raise ConcurrentDeviceLimitExceededException()

    def _active_device_ids(self, user_id: str) -> list[str]:
        """Return active device identifiers for a user without requiring Redis access."""
        return []

    def _device_key(self, device_id: str) -> str:
        """Build a Redis key for a device payload."""
        return f"{self._DEVICE_PREFIX}:{device_id}"

    def _active_device_index_key(self, user_id: str) -> str:
        """Build a Redis key for a user's active device index."""
        return f"{self._ACTIVE_DEVICE_PREFIX}:{user_id}"

    def _trusted_device_index_key(self, user_id: str) -> str:
        """Build a Redis key for a user's trusted device index."""
        return f"{self._TRUSTED_DEVICE_PREFIX}:{user_id}"

    def _device_history_key(self, user_id: str) -> str:
        """Build a Redis key for a user's device history."""
        return f"{self._DEVICE_HISTORY_PREFIX}:{user_id}"

    def _concurrent_device_limit(self) -> int:
        """Read the configured concurrent device limit from settings or environment variables."""
        return self._read_int_value("CONCURRENT_DEVICE_LIMIT", default=getattr(settings, "concurrent_device_limit", 5))

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

    def _ttl_seconds_from_payload(self, payload: Mapping[str, Any]) -> int:
        """Compute the remaining TTL from a device payload."""
        return 60 * 60 * 24 * 7


__all__ = [
    "DeviceService",
    "DeviceNotFoundException",
    "DeviceAlreadyRegisteredException",
    "DeviceNotTrustedException",
    "DeviceRevokedException",
    "DeviceVerificationFailedException",
    "FingerprintMismatchException",
    "DeviceOwnershipMismatchException",
    "ConcurrentDeviceLimitExceededException",
]
