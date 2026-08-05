from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.repositories.device_repository import DeviceRepository
from app.repositories.user_repository import UserRepository
from app.utils.exceptions import DatabaseException, ValidationException


class DeviceService:
    """Coordinate trusted-device lifecycle and ownership workflows."""

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        device_repository: DeviceRepository,
        audit_service: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.device_repository = device_repository
        self.audit_service = audit_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def register_device(
        self,
        *,
        user_id: UUID,
        device_fingerprint: str,
        device_name: str | None = None,
        device_type: str = "unknown",
        operating_system: str | None = None,
        os_version: str | None = None,
        app_version: str | None = None,
        browser_name: str | None = None,
        browser_version: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Register and trust a new device for the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.device_repository)
        self._validate_required_fields(device_fingerprint=device_fingerprint)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        existing_device = await self.device_repository.get_device_by_identifier(device_fingerprint)
        if existing_device and existing_device.user_id != user_id:
            raise ValidationException("Device fingerprint already belongs to another user.")

        try:
            async with self._session_scope():
                if existing_device:
                    existing_device.user_id = user_id
                    existing_device.device_name = device_name
                    existing_device.device_type = device_type
                    existing_device.operating_system = operating_system
                    existing_device.os_version = os_version
                    existing_device.app_version = app_version
                    existing_device.browser_name = browser_name
                    existing_device.browser_version = browser_version
                    existing_device.ip_address = ip_address
                    existing_device.is_trusted = True
                    existing_device.is_active = True
                    existing_device.is_revoked = False
                    existing_device.last_seen_at = datetime.now(timezone.utc)
                    await self.device_repository.update_device(existing_device)
                    device = existing_device
                else:
                    device = Device(
                        user_id=user_id,
                        device_name=device_name,
                        device_type=device_type,
                        operating_system=operating_system,
                        os_version=os_version,
                        app_version=app_version,
                        browser_name=browser_name,
                        browser_version=browser_version,
                        device_fingerprint=device_fingerprint,
                        ip_address=ip_address,
                        is_trusted=True,
                        is_active=True,
                        is_revoked=False,
                    )
                    await self.device_repository.create_device(device)

                await self._log_event("device_registered", user_id=user.id)
                return self._serialize_device(device)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Device registration failed.") from exc

    async def get_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Return a single device owned by the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.device_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        device = await self.device_repository.get_device_by_id(device_id)
        if not device or device.user_id != user_id:
            raise ValidationException("Device not found.")
        return self._serialize_device(device)

    async def list_devices(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return paginated devices belonging to the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.device_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        devices, total = await self.device_repository.get_user_devices(user_id=user_id, page=page, page_size=page_size)
        return {
            "items": [self._serialize_device(device) for device in devices],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def update_device(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        device_name: str | None = None,
        device_type: str | None = None,
        operating_system: str | None = None,
        os_version: str | None = None,
        app_version: str | None = None,
        browser_name: str | None = None,
        browser_version: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Update editable metadata for a user-owned device."""
        self._require_repository(self.user_repository)
        self._require_repository(self.device_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        device = await self.device_repository.get_device_by_id(device_id)
        if not device or device.user_id != user_id:
            raise ValidationException("Device not found.")

        try:
            async with self._session_scope():
                if device_name is not None:
                    device.device_name = self._sanitize_text(device_name, field_name="device_name")
                if device_type is not None:
                    device.device_type = self._sanitize_text(device_type, field_name="device_type")
                if operating_system is not None:
                    device.operating_system = self._sanitize_text(operating_system, field_name="operating_system")
                if os_version is not None:
                    device.os_version = self._sanitize_text(os_version, field_name="os_version")
                if app_version is not None:
                    device.app_version = self._sanitize_text(app_version, field_name="app_version")
                if browser_name is not None:
                    device.browser_name = self._sanitize_text(browser_name, field_name="browser_name")
                if browser_version is not None:
                    device.browser_version = self._sanitize_text(browser_version, field_name="browser_version")
                if ip_address is not None:
                    device.ip_address = self._sanitize_text(ip_address, field_name="ip_address")

                await self.device_repository.update_device(device)
                await self._log_event("device_updated", user_id=user.id)
                return self._serialize_device(device)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Device update failed.") from exc

    async def trust_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Mark a device as trusted for the authenticated user."""
        device = await self._get_owned_device(user_id=user_id, device_id=device_id)
        try:
            async with self._session_scope():
                device.is_trusted = True
                device.is_active = True
                device.is_revoked = False
                device.revoked_reason = None
                device.revoked_at = None
                await self.device_repository.update_device(device, is_trusted=True, is_active=True, is_revoked=False, revoked_reason=None, revoked_at=None)
                await self._log_event("device_trusted", user_id=user_id)
                return self._serialize_device(device)
        except Exception as exc:
            raise DatabaseException("Device trust update failed.") from exc

    async def untrust_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Remove trusted status from a device owned by the authenticated user."""
        device = await self._get_owned_device(user_id=user_id, device_id=device_id)
        try:
            async with self._session_scope():
                device.is_trusted = False
                await self.device_repository.update_device(device, is_trusted=False)
                await self._log_event("device_untrusted", user_id=user_id)
                return self._serialize_device(device)
        except Exception as exc:
            raise DatabaseException("Device untrust update failed.") from exc

    async def revoke_device(self, *, user_id: UUID, device_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Deactivate and revoke a user-owned device."""
        device = await self._get_owned_device(user_id=user_id, device_id=device_id)
        try:
            async with self._session_scope():
                device.is_active = False
                device.is_revoked = True
                device.revoked_reason = self._sanitize_text(reason, field_name="reason") if reason is not None else None
                device.revoked_at = datetime.now(timezone.utc)
                device.is_trusted = False
                await self.device_repository.deactivate_device(device, reason=device.revoked_reason)
                await self._log_event("device_revoked", user_id=user_id)
                return self._serialize_device(device)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Device revocation failed.") from exc

    async def delete_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Soft-delete a user-owned device by deactivating it."""
        device = await self._get_owned_device(user_id=user_id, device_id=device_id)
        try:
            async with self._session_scope():
                device.is_active = False
                device.is_revoked = True
                device.revoked_reason = "deleted"
                await self.device_repository.deactivate_device(device, reason="deleted")
                await self._log_event("device_deleted", user_id=user_id)
                return self._serialize_device(device)
        except Exception as exc:
            raise DatabaseException("Device deletion failed.") from exc

    async def is_trusted_device(self, *, user_id: UUID, device_id: UUID) -> bool:
        """Return whether a user-owned device is trusted and active."""
        device = await self._get_owned_device(user_id=user_id, device_id=device_id)
        return bool(device.is_trusted and device.is_active and not device.is_revoked)

    async def _get_owned_device(self, *, user_id: UUID, device_id: UUID) -> Device:
        self._require_repository(self.user_repository)
        self._require_repository(self.device_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        device = await self.device_repository.get_device_by_id(device_id)
        if not device or device.user_id != user_id:
            raise ValidationException("Device not found.")
        return device

    def _validate_required_fields(self, **values: Any) -> None:
        for field_name, value in values.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationException(f"{field_name} is required.")

    def _sanitize_text(self, value: str | None, *, field_name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationException(f"{field_name} must be a string.")
        sanitized = value.strip()
        if not sanitized:
            return None
        if len(sanitized) > 255:
            raise ValidationException(f"{field_name} is too long.")
        return sanitized

    def _serialize_device(self, device: Device) -> dict[str, Any]:
        return {
            "id": str(device.id),
            "device_name": device.device_name,
            "device_type": device.device_type,
            "operating_system": device.operating_system,
            "os_version": device.os_version,
            "app_version": device.app_version,
            "browser_name": device.browser_name,
            "browser_version": device.browser_version,
            "is_trusted": device.is_trusted,
            "is_active": device.is_active,
            "is_revoked": device.is_revoked,
            "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
            "created_at": device.created_at.isoformat() if device.created_at else None,
        }

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "device_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if callable(getattr(self, "audit_service", None)):
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for DeviceService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
