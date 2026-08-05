from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from app.config.redis import get_redis
from app.config.settings import settings
from app.services.auth.device_service import (
    DeviceAlreadyRegisteredException,
    DeviceNotFoundException,
    DeviceNotTrustedException,
    DeviceRevokedException,
    DeviceVerificationFailedException,
    FingerprintMismatchException,
)
from app.utils.exceptions import DatabaseException, ValidationException
from app.utils.logger import log_audit_event, log_security_event


class TrustedDeviceService:
    """Enterprise device recognition, trust management, and risk evaluation service.

    This service is intentionally limited to device lifecycle and security
    assessment concerns. Authentication and route/controller orchestration remain
    outside its responsibility.
    """

    _DEVICE_PREFIX = "device"
    _ACTIVE_DEVICE_PREFIX = "active_device"
    _TRUSTED_DEVICE_PREFIX = "trusted_device"
    _DEVICE_HISTORY_PREFIX = "device_history"

    def __init__(
        self,
        *,
        user_repository: Any | None = None,
        device_repository: Any | None = None,
        risk_engine_service: Any | None = None,
        redis_client: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with injectable collaborators."""
        self.user_repository = user_repository
        self.device_repository = device_repository
        self.risk_engine_service = risk_engine_service
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)

    async def register_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_info: Mapping[str, Any] | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        is_trusted: bool | None = None,
        trust_until: datetime | None = None,
        **extra_fields: Any,
    ) -> dict[str, Any]:
        """Register or refresh a device record for a user."""
        user_id_value = self._coerce_user_id(user_id)
        normalized = self._normalize_device_info(
            device_info or {},
            user_id=user_id_value,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            metadata=metadata,
            extra_fields=extra_fields,
        )

        if not user_id_value:
            raise ValidationException("User identifier is required.")
        if self.user_repository is not None:
            user = await self._resolve_user(user_id_value)
            if user is None:
                raise ValidationException("User not found.")

        existing = await self._find_existing_device(
            user_id=user_id_value,
            device_id=normalized.get("device_id"),
            device_fingerprint=normalized.get("device_fingerprint"),
        )
        if existing is not None:
            return await self._update_device_record(existing, normalized, metadata=metadata, is_trusted=is_trusted)

        now = datetime.now(timezone.utc)
        record = {
            "device_id": normalized.get("device_id") or str(uuid4()),
            "user_id": str(user_id_value),
            "device_name": normalized.get("device_name") or "Unknown Device",
            "device_type": normalized.get("device_type") or "unknown",
            "browser": normalized.get("browser"),
            "browser_version": normalized.get("browser_version"),
            "operating_system": normalized.get("operating_system"),
            "os_version": normalized.get("os_version"),
            "platform": normalized.get("platform"),
            "screen_resolution": normalized.get("screen_resolution"),
            "user_agent": normalized.get("user_agent"),
            "language": normalized.get("language"),
            "timezone": normalized.get("timezone"),
            "is_mobile": normalized.get("is_mobile"),
            "push_notification_token": normalized.get("push_notification_token"),
            "device_fingerprint": normalized.get("device_fingerprint"),
            "ip_address": normalized.get("ip_address"),
            "country": normalized.get("country"),
            "city": normalized.get("city"),
            "first_seen_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "last_login_at": now.isoformat(),
            "is_active": True,
            "is_trusted": bool(is_trusted),
            "is_revoked": False,
            "trust_status": "trusted" if is_trusted else "untrusted",
            "verification_status": "verified" if is_trusted else "pending",
            "trusted_until": trust_until.isoformat() if trust_until else None,
            "metadata": dict(normalized.get("metadata") or {}),
            "risk_level": "Low Risk",
            "risk_score": 0,
            "fingerprint_changes": [],
            "activity_count": 1,
            "device_age_days": 0,
        }

        await self._persist_device_record(record)
        await self._record_history(user_id=str(user_id_value), device_id=record["device_id"], action="registered")
        self.logger.info(
            "device_registered",
            extra={"event_type": "security", "user_id": str(user_id_value), "device_id": record["device_id"], "risk_level": record["risk_level"]},
        )
        return {"device": record, "status": "registered", "is_new_device": True, "risk_level": record["risk_level"]}

    async def trust_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
        expires_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mark a device as trusted."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        if record.get("is_revoked"):
            raise DeviceRevokedException()

        record["is_trusted"] = True
        record["trust_status"] = "trusted"
        record["verification_status"] = "verified"
        record["trusted_until"] = expires_at.isoformat() if expires_at else None
        if metadata:
            record.setdefault("metadata", {}).update(dict(metadata))
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="trusted")
        self.logger.info(
            "device_trusted",
            extra={"event_type": "security", "user_id": record.get("user_id"), "device_id": record.get("device_id")},
        )
        return {"device": record, "status": "trusted", "trusted": True}

    async def untrust_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> dict[str, Any]:
        """Remove trust from a device."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        record["is_trusted"] = False
        record["trust_status"] = "untrusted"
        record["verification_status"] = "pending"
        record["trusted_until"] = None
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="untrusted")
        return {"device": record, "status": "untrusted", "trusted": False}

    async def verify_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_info: Mapping[str, Any] | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        **extra_fields: Any,
    ) -> dict[str, Any]:
        """Evaluate the current device state and produce a structured assessment."""
        user_id_value = self._coerce_user_id(user_id)
        normalized = self._normalize_device_info(
            device_info or {},
            user_id=user_id_value,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            metadata=metadata,
            extra_fields=extra_fields,
        )
        if not user_id_value:
            raise ValidationException("User identifier is required.")

        existing = await self._find_existing_device(
            user_id=user_id_value,
            device_id=normalized.get("device_id"),
            device_fingerprint=normalized.get("device_fingerprint"),
        )
        if existing is None:
            result = await self.register_device(
                user_id=user_id_value,
                device_info=normalized,
                device_id=normalized.get("device_id"),
                device_fingerprint=normalized.get("device_fingerprint"),
                metadata=metadata,
                is_trusted=False,
            )
            assessment = {
                "device": result["device"],
                "status": "new_device",
                "is_new_device": True,
                "is_trusted": False,
                "risk_level": "Low Risk",
                "risk_score": 0,
                "changes": [],
                "verified": True,
            }
            return assessment

        if existing.get("is_revoked"):
            raise DeviceRevokedException()

        changes = await self.detect_device_change(existing, normalized)
        risk_result = await self.calculate_device_risk(existing, normalized, changes=changes)
        updated_record = dict(existing)
        updated_record["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        updated_record["last_login_at"] = datetime.now(timezone.utc).isoformat()
        updated_record["activity_count"] = int(existing.get("activity_count") or 0) + 1
        updated_record["browser"] = normalized.get("browser") or updated_record.get("browser")
        updated_record["browser_version"] = normalized.get("browser_version") or updated_record.get("browser_version")
        updated_record["operating_system"] = normalized.get("operating_system") or updated_record.get("operating_system")
        updated_record["os_version"] = normalized.get("os_version") or updated_record.get("os_version")
        updated_record["platform"] = normalized.get("platform") or updated_record.get("platform")
        updated_record["screen_resolution"] = normalized.get("screen_resolution") or updated_record.get("screen_resolution")
        updated_record["user_agent"] = normalized.get("user_agent") or updated_record.get("user_agent")
        updated_record["language"] = normalized.get("language") or updated_record.get("language")
        updated_record["timezone"] = normalized.get("timezone") or updated_record.get("timezone")
        updated_record["is_mobile"] = normalized.get("is_mobile") if normalized.get("is_mobile") is not None else updated_record.get("is_mobile")
        updated_record["country"] = normalized.get("country") or updated_record.get("country")
        updated_record["city"] = normalized.get("city") or updated_record.get("city")
        updated_record["ip_address"] = normalized.get("ip_address") or updated_record.get("ip_address")
        updated_record["metadata"] = {**dict(existing.get("metadata") or {}), **dict(normalized.get("metadata") or {})}
        updated_record["risk_level"] = risk_result["risk_level"]
        updated_record["risk_score"] = risk_result["risk_score"]
        updated_record["fingerprint_changes"] = changes.get("changes") or []
        await self._persist_device_record(updated_record)
        await self._record_history(user_id=updated_record.get("user_id"), device_id=updated_record.get("device_id"), action="verified")

        if risk_result["risk_level"] in {"High Risk", "Critical Risk"}:
            log_security_event(
                self.logger,
                "device_risk_detected",
                extra={"event_type": "security", "user_id": str(user_id_value), "device_id": updated_record.get("device_id"), "risk_level": risk_result["risk_level"]},
            )

        return {
            "device": updated_record,
            "status": "verified",
            "is_new_device": False,
            "is_trusted": self.is_trusted_device(updated_record),
            "risk_level": risk_result["risk_level"],
            "risk_score": risk_result["risk_score"],
            "changes": changes.get("changes") or [],
            "verified": True,
        }

    async def get_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve a single device by identifier."""
        return await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )

    async def get_user_devices(self, *, user_id: UUID | str | None = None) -> list[dict[str, Any]]:
        """Return all devices registered for a user."""
        user_id_value = self._coerce_user_id(user_id)
        if not user_id_value:
            raise ValidationException("User identifier is required.")

        if self.device_repository is not None:
            try:
                result = await self.device_repository.get_user_devices(user_id=user_id_value)
            except TypeError:
                result = await self.device_repository.get_user_devices(user_id=str(user_id_value))
            if isinstance(result, tuple):
                records = result[0]
            else:
                records = result
            return [self._serialize_device_record(record) for record in records]

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise DatabaseException("Redis is unavailable for device lookup.")
        device_ids = await redis_client.smembers(self._user_index_key(user_id_value))
        devices: list[dict[str, Any]] = []
        for device_id in device_ids:
            try:
                devices.append(await self._get_device_record(user_id=user_id_value, device_id=str(device_id)))
            except (DeviceNotFoundException, DeviceRevokedException):
                continue
        return devices

    async def remove_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> bool:
        """Remove a device record from storage."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        if self.device_repository is not None and hasattr(self.device_repository, "delete_device"):
            await self.device_repository.delete_device(record.get("device_id"))
        redis_client = await self._get_redis_client()
        if redis_client is not None:
            await redis_client.delete(self._device_key(record.get("device_id")))
            await redis_client.srem(self._user_index_key(record.get("user_id")), record.get("device_id"))
            await redis_client.srem(self._trusted_index_key(record.get("user_id")), record.get("device_id"))
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="removed")
        return True

    async def revoke_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Revoke a device and mark it inactive."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        record["is_active"] = False
        record["is_revoked"] = True
        record["is_trusted"] = False
        record["trust_status"] = "revoked"
        record["verification_status"] = "revoked"
        record["revoked_at"] = datetime.now(timezone.utc).isoformat()
        record["revoked_reason"] = reason or "revoked_by_admin"
        record["trusted_until"] = None
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="revoked")
        self.logger.info(
            "device_revoked",
            extra={"event_type": "security", "user_id": record.get("user_id"), "device_id": record.get("device_id"), "reason": record.get("revoked_reason")},
        )
        return {"device": record, "status": "revoked"}

    async def update_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update mutable device attributes."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        if not updates:
            return {"device": record, "status": "unchanged"}
        normalized_updates = dict(updates)
        for key, value in normalized_updates.items():
            if value is None:
                continue
            record[key] = value
        record["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="updated")
        return {"device": record, "status": "updated"}

    async def record_device_activity(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record recent device activity and bump counters."""
        record = await self._get_device_record(
            user_id=user_id,
            device_id=device_id,
            device_identifier=device_identifier,
        )
        now = datetime.now(timezone.utc)
        record["last_seen_at"] = now.isoformat()
        record["activity_count"] = int(record.get("activity_count") or 0) + 1
        if metadata:
            record.setdefault("metadata", {}).update(dict(metadata))
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="activity")
        return {"device": record, "status": "activity_recorded"}

    async def calculate_device_risk(
        self,
        existing_device: Mapping[str, Any] | None = None,
        current_device: Mapping[str, Any] | None = None,
        *,
        changes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Calculate a configurable device risk level and supporting factors."""
        if self.risk_engine_service is not None:
            try:
                return await self.risk_engine_service.calculate_device_risk(existing_device, current_device, changes=changes)
            except TypeError:
                pass

        existing = dict(existing_device or {})
        current = dict(current_device or {})
        score = 0
        factors: list[str] = []
        if existing.get("device_fingerprint") and current.get("device_fingerprint") and existing.get("device_fingerprint") != current.get("device_fingerprint"):
            score += 35
            factors.append("fingerprint_mismatch")
        if (existing.get("browser") or "") != (current.get("browser") or ""):
            score += 15
            factors.append("browser_change")
        if (existing.get("operating_system") or "") != (current.get("operating_system") or ""):
            score += 15
            factors.append("os_change")
        if (existing.get("country") or "") != (current.get("country") or ""):
            score += 10
            factors.append("country_change")
        if (existing.get("ip_address") or "") != (current.get("ip_address") or ""):
            score += 10
            factors.append("ip_change")
        if existing.get("is_trusted") is False and current.get("is_trusted") is False:
            score += 10
            factors.append("new_or_untrusted_device")
        if changes and changes.get("changes"):
            score += min(20, len(changes.get("changes")) * 5)
            factors.append("device_change")
        if current.get("is_mobile") is True and existing.get("is_mobile") is not True:
            score += 5
            factors.append("device_type_change")
        if current.get("push_notification_token") and existing.get("push_notification_token") != current.get("push_notification_token"):
            score += 10
            factors.append("token_change")

        risk_level = self._risk_score_to_level(score)
        return {"risk_score": score, "risk_level": risk_level, "factors": factors}

    async def detect_device_change(
        self,
        existing_device: Mapping[str, Any] | None = None,
        current_device: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare fingerprints and device metadata to surface suspicious changes."""
        existing = dict(existing_device or {})
        current = dict(current_device or {})
        changes: list[str] = []
        if existing.get("device_fingerprint") and current.get("device_fingerprint") and existing.get("device_fingerprint") != current.get("device_fingerprint"):
            changes.append("fingerprint_mismatch")
        for field in ("browser", "operating_system", "platform", "screen_resolution", "country", "ip_address"):
            if (existing.get(field) or "") != (current.get(field) or ""):
                changes.append(field)
        return {"changed": bool(changes), "changes": changes}

    async def is_new_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> bool:
        """Determine whether a device is newly observed."""
        try:
            record = await self._get_device_record(
                user_id=user_id,
                device_id=device_id,
                device_identifier=device_identifier,
            )
        except DeviceNotFoundException:
            return True
        return bool(not record.get("device_id")) or not record.get("first_seen_at")

    async def is_trusted_device(
        self,
        device: Mapping[str, Any] | None = None,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> bool:
        """Return whether a device is trusted and not expired."""
        if isinstance(device, Mapping):
            record = dict(device)
        else:
            try:
                record = await self._get_device_record(
                    user_id=user_id,
                    device_id=device_id,
                    device_identifier=device_identifier,
                )
            except DeviceNotFoundException:
                return False

        if record.get("is_revoked"):
            return False
        if not record.get("is_trusted"):
            return False
        trusted_until = record.get("trusted_until")
        if not trusted_until:
            return True
        try:
            expires_at = datetime.fromisoformat(str(trusted_until))
        except ValueError:
            return True
        return expires_at >= datetime.now(timezone.utc)

    async def get_device_statistics(self, *, user_id: UUID | str | None = None) -> dict[str, Any]:
        """Return aggregate device statistics for a user or system-wide scope."""
        user_id_value = self._coerce_user_id(user_id)
        if self.device_repository is not None and hasattr(self.device_repository, "get_user_devices"):
            devices = await self.get_user_devices(user_id=user_id_value) if user_id_value else []
            return {
                "total_devices": len(devices),
                "trusted_devices": sum(1 for device in devices if device.get("is_trusted")),
                "revoked_devices": sum(1 for device in devices if device.get("is_revoked")),
                "active_devices": sum(1 for device in devices if device.get("is_active")),
            }

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise DatabaseException("Redis is unavailable for device statistics.")
        if user_id_value:
            device_ids = await redis_client.smembers(self._user_index_key(user_id_value))
            devices = [await self._get_device_record(user_id=user_id_value, device_id=str(device_id)) for device_id in device_ids]
        else:
            devices = []
        return {
            "total_devices": len(devices),
            "trusted_devices": sum(1 for device in devices if device.get("is_trusted")),
            "revoked_devices": sum(1 for device in devices if device.get("is_revoked")),
            "active_devices": sum(1 for device in devices if device.get("is_active")),
        }

    async def verify_trusted_device(self, *, user_id: UUID | str | None = None, **payload: Any) -> dict[str, Any]:
        """Compatibility wrapper for the SecurityService facade."""
        return await self.verify_device(user_id=user_id, device_info=payload)

    async def remove_trusted_device(self, *, user_id: UUID | str | None = None, **payload: Any) -> bool:
        """Compatibility wrapper for the SecurityService facade."""
        device_id = payload.get("device_id")
        device_identifier = payload.get("device_identifier")
        return await self.remove_device(user_id=user_id, device_id=device_id, device_identifier=device_identifier)

    async def _update_device_record(
        self,
        existing: Mapping[str, Any],
        normalized: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
        is_trusted: bool | None = None,
    ) -> dict[str, Any]:
        """Refresh an existing device record with new fingerprint information."""
        record = dict(existing)
        record["device_name"] = normalized.get("device_name") or record.get("device_name")
        record["device_type"] = normalized.get("device_type") or record.get("device_type")
        record["browser"] = normalized.get("browser") or record.get("browser")
        record["browser_version"] = normalized.get("browser_version") or record.get("browser_version")
        record["operating_system"] = normalized.get("operating_system") or record.get("operating_system")
        record["os_version"] = normalized.get("os_version") or record.get("os_version")
        record["platform"] = normalized.get("platform") or record.get("platform")
        record["screen_resolution"] = normalized.get("screen_resolution") or record.get("screen_resolution")
        record["user_agent"] = normalized.get("user_agent") or record.get("user_agent")
        record["language"] = normalized.get("language") or record.get("language")
        record["timezone"] = normalized.get("timezone") or record.get("timezone")
        record["is_mobile"] = normalized.get("is_mobile") if normalized.get("is_mobile") is not None else record.get("is_mobile")
        record["push_notification_token"] = normalized.get("push_notification_token") or record.get("push_notification_token")
        record["ip_address"] = normalized.get("ip_address") or record.get("ip_address")
        record["country"] = normalized.get("country") or record.get("country")
        record["city"] = normalized.get("city") or record.get("city")
        if metadata:
            record.setdefault("metadata", {}).update(dict(metadata))
        if is_trusted is not None:
            record["is_trusted"] = bool(is_trusted)
            record["trust_status"] = "trusted" if is_trusted else "untrusted"
            record["verification_status"] = "verified" if is_trusted else "pending"
        record["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        record["activity_count"] = int(record.get("activity_count") or 0) + 1
        await self._persist_device_record(record)
        await self._record_history(user_id=record.get("user_id"), device_id=record.get("device_id"), action="refreshed")
        return {"device": record, "status": "refreshed", "is_new_device": False, "risk_level": record.get("risk_level") or "Low Risk"}

    async def _find_existing_device(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve an existing device from repository or Redis."""
        user_id_value = self._coerce_user_id(user_id)
        if self.device_repository is not None:
            if device_id and hasattr(self.device_repository, "get_device_by_id"):
                try:
                    record = await self.device_repository.get_device_by_id(device_id)
                except TypeError:
                    record = await self.device_repository.get_device_by_id(UUID(str(device_id)))
                if record is not None:
                    return self._serialize_device_record(record)
            if device_fingerprint and hasattr(self.device_repository, "get_device_by_identifier"):
                record = await self.device_repository.get_device_by_identifier(device_fingerprint)
                if record is not None:
                    return self._serialize_device_record(record)

        if not user_id_value:
            return None
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return None
        device_ids = await redis_client.smembers(self._user_index_key(user_id_value))
        for device_key in device_ids:
            try:
                candidate = await self._get_device_record(user_id=user_id_value, device_id=str(device_key))
            except (DeviceNotFoundException, DeviceRevokedException):
                continue
            if device_id and candidate.get("device_id") == str(device_id):
                return candidate
            if device_fingerprint and candidate.get("device_fingerprint") == device_fingerprint:
                return candidate
        return None

    async def _get_device_record(
        self,
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_identifier: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve a device record from the repository or Redis cache."""
        user_id_value = self._coerce_user_id(user_id)
        if self.device_repository is not None:
            if device_id and hasattr(self.device_repository, "get_device_by_id"):
                try:
                    record = await self.device_repository.get_device_by_id(device_id)
                except TypeError:
                    record = await self.device_repository.get_device_by_id(UUID(str(device_id)))
                if record is not None:
                    return self._serialize_device_record(record)
            if device_identifier and hasattr(self.device_repository, "get_device_by_identifier"):
                record = await self.device_repository.get_device_by_identifier(device_identifier)
                if record is not None:
                    return self._serialize_device_record(record)

        if device_id:
            redis_client = await self._get_redis_client()
            if redis_client is not None:
                raw = await redis_client.get(self._device_key(device_id))
                if raw:
                    payload = json.loads(raw)
                    if payload.get("is_revoked"):
                        raise DeviceRevokedException()
                    return payload
            raise DeviceNotFoundException()

        if device_identifier:
            redis_client = await self._get_redis_client()
            if redis_client is not None:
                device_ids = await redis_client.smembers(self._user_index_key(user_id_value)) if user_id_value else []
                for candidate_id in device_ids:
                    payload = await self._load_device_payload(str(candidate_id))
                    if payload and payload.get("device_fingerprint") == device_identifier:
                        return payload
            raise DeviceNotFoundException()

        raise DeviceNotFoundException()

    async def _persist_device_record(self, record: Mapping[str, Any]) -> None:
        """Persist a device payload to repository and Redis where available."""
        payload = self._serialize_device_record(record)
        if self.device_repository is not None:
            if payload.get("device_id") and hasattr(self.device_repository, "update_device"):
                try:
                    await self.device_repository.update_device(payload)
                except TypeError:
                    pass
            elif payload.get("device_id") and hasattr(self.device_repository, "create_device"):
                try:
                    await self.device_repository.create_device(payload)
                except TypeError:
                    pass

        redis_client = await self._get_redis_client()
        if redis_client is not None:
            await redis_client.set(self._device_key(payload.get("device_id")), json.dumps(payload), ex=self._cache_ttl_seconds())
            if payload.get("user_id"):
                await redis_client.sadd(self._user_index_key(payload.get("user_id")), payload.get("device_id"))
                if payload.get("is_trusted"):
                    await redis_client.sadd(self._trusted_index_key(payload.get("user_id")), payload.get("device_id"))
                else:
                    await redis_client.srem(self._trusted_index_key(payload.get("user_id")), payload.get("device_id"))

    async def _record_history(self, *, user_id: str | None, device_id: str | None, action: str) -> None:
        """Store a lightweight security-event history entry for the device."""
        if not user_id or not device_id:
            return
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return
        payload = {
            "user_id": user_id,
            "device_id": device_id,
            "action": action,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.lpush(self._history_key(user_id), json.dumps(payload))
        await redis_client.ltrim(self._history_key(user_id), 0, 19)
        log_audit_event(self.logger, "device_history_updated", user_id=user_id, device_id=device_id, action=action)

    async def _get_redis_client(self) -> Any:
        """Return a Redis client from injection or shared application state."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:
            self.logger.warning("redis_unavailable", extra={"event_type": "security", "error": str(exc)})
            return None

    async def _resolve_user(self, user_id: UUID | str) -> Any | None:
        """Resolve a user record from the injected repository if available."""
        if self.user_repository is None:
            return None
        try:
            return await self.user_repository.get_by_id(user_id)
        except TypeError:
            return await self.user_repository.get_by_id(UUID(str(user_id)))

    def _normalize_device_info(
        self,
        device_info: Mapping[str, Any],
        *,
        user_id: UUID | str | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        extra_fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Normalize raw device details into a stable payload."""
        normalized: dict[str, Any] = {}
        if extra_fields:
            normalized.update(dict(extra_fields))
        if device_info:
            normalized.update(dict(device_info))

        browser = normalized.get("browser") or normalized.get("browser_name") or normalized.get("browser_name")
        operating_system = normalized.get("operating_system") or normalized.get("os") or normalized.get("os_name")
        device_name = normalized.get("device_name") or normalized.get("name")
        device_type = normalized.get("device_type") or normalized.get("deviceClass") or normalized.get("kind")
        platform = normalized.get("platform") or normalized.get("platform_name")
        screen_resolution = normalized.get("screen_resolution") or normalized.get("resolution")
        language = normalized.get("language") or normalized.get("locale")
        timezone_name = normalized.get("timezone") or normalized.get("time_zone")
        user_agent = normalized.get("user_agent") or normalized.get("userAgent")
        push_notification_token = normalized.get("push_notification_token") or normalized.get("push_token")
        is_mobile = normalized.get("is_mobile")
        if is_mobile is None:
            is_mobile = normalized.get("mobile")

        normalized["user_id"] = user_id
        normalized["device_id"] = device_id or normalized.get("device_id")
        normalized["device_name"] = device_name
        normalized["browser"] = browser
        normalized["browser_version"] = normalized.get("browser_version") or normalized.get("browserVersion")
        normalized["operating_system"] = operating_system
        normalized["os_version"] = normalized.get("os_version") or normalized.get("osVersion")
        normalized["device_type"] = device_type
        normalized["platform"] = platform
        normalized["screen_resolution"] = screen_resolution
        normalized["user_agent"] = user_agent
        normalized["language"] = language
        normalized["timezone"] = timezone_name
        normalized["is_mobile"] = is_mobile
        normalized["push_notification_token"] = push_notification_token
        normalized["ip_address"] = normalized.get("ip_address") or normalized.get("ip")
        normalized["country"] = normalized.get("country") or normalized.get("location_country")
        normalized["city"] = normalized.get("city") or normalized.get("location_city")
        normalized["metadata"] = dict(metadata or {})
        fingerprint_payload = {
            "device_id": normalized.get("device_id"),
            "device_name": normalized.get("device_name"),
            "browser": normalized.get("browser"),
            "browser_version": normalized.get("browser_version"),
            "operating_system": normalized.get("operating_system"),
            "os_version": normalized.get("os_version"),
            "device_type": normalized.get("device_type"),
            "platform": normalized.get("platform"),
            "screen_resolution": normalized.get("screen_resolution"),
            "user_agent": normalized.get("user_agent"),
            "language": normalized.get("language"),
            "timezone": normalized.get("timezone"),
            "is_mobile": normalized.get("is_mobile"),
            "push_notification_token": normalized.get("push_notification_token"),
            "device_fingerprint": device_fingerprint,
        }
        fingerprint_payload = {key: value for key, value in fingerprint_payload.items() if value not in (None, "", [], {})}
        normalized["device_fingerprint"] = device_fingerprint or self._hash_fingerprint(fingerprint_payload)
        return normalized

    def _hash_fingerprint(self, payload: Mapping[str, Any]) -> str:
        """Generate a stable fingerprint from a normalized payload."""
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _serialize_device_record(self, record: Any) -> dict[str, Any]:
        """Convert repository ORM objects or plain dictionaries into a dict payload."""
        if isinstance(record, dict):
            return dict(record)
        if hasattr(record, "__dict__"):
            payload = {key: getattr(record, key) for key in record.__dict__.keys() if not key.startswith("_")}
            if "metadata_payload" in payload and "metadata" not in payload:
                payload["metadata"] = payload.get("metadata_payload")
            return payload
        return {}

    def _coerce_user_id(self, user_id: UUID | str | None) -> UUID | str | None:
        """Normalize a user identifier into a string-compatible form."""
        if user_id is None:
            return None
        if isinstance(user_id, UUID):
            return user_id
        return str(user_id)

    def _risk_score_to_level(self, score: int) -> str:
        """Map a numeric risk score to the expected business risk tiers."""
        if score >= 80:
            return "Critical Risk"
        if score >= 55:
            return "High Risk"
        if score >= 25:
            return "Medium Risk"
        return "Low Risk"

    async def _load_device_payload(self, device_id: str) -> dict[str, Any] | None:
        """Load a device payload from Redis if present."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return None
        raw = await redis_client.get(self._device_key(device_id))
        if not raw:
            return None
        return json.loads(raw)

    def _device_key(self, device_id: str | None) -> str:
        """Return the Redis key for a device payload."""
        return f"{self._DEVICE_PREFIX}:{device_id}"

    def _user_index_key(self, user_id: UUID | str | None) -> str:
        """Return the Redis set key for devices belonging to a user."""
        return f"{self._ACTIVE_DEVICE_PREFIX}:{user_id}"

    def _trusted_index_key(self, user_id: UUID | str | None) -> str:
        """Return the Redis set key for trusted devices belonging to a user."""
        return f"{self._TRUSTED_DEVICE_PREFIX}:{user_id}"

    def _history_key(self, user_id: UUID | str | None) -> str:
        """Return the Redis history key for device activity."""
        return f"{self._DEVICE_HISTORY_PREFIX}:{user_id}"

    def _cache_ttl_seconds(self) -> int:
        """Return the configured device cache TTL in seconds."""
        configured_ttl = getattr(settings, "device_cache_ttl_seconds", None)
        if configured_ttl is None:
            configured_ttl = getattr(settings, "redis_cache_ttl", 300)
        return int(configured_ttl)


__all__ = ["TrustedDeviceService"]
