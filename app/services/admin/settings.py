from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.system_settings import SystemSettings
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.utils.exceptions import AuthorizationException, ValidationException


class SettingsService:
    """Service for system configuration and settings."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        settings_repository: SystemSettingsRepository | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.settings_repository = settings_repository
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.session = session

    async def get_settings(self, **payload: Any) -> dict[str, Any]:
        if self.settings_repository is None:
            raise ValidationException(detail="System settings persistence is unavailable.", error_code="SETTINGS_UNAVAILABLE")

        page = payload.get("page") or 1
        page_size = payload.get("page_size") or 50
        category = payload.get("category")
        is_active = payload.get("is_active")
        items, total = await self.settings_repository.get_all_settings(
            category=category,
            is_active=is_active,
            page=page,
            page_size=page_size,
        )
        return {
            "success": True,
            "data": {
                "items": [self._serialize_setting(item) for item in items],
                "total": total,
            },
            "meta": {"source": "admin.settings", "page": page, "page_size": page_size},
        }

    async def update_settings(self, **payload: Any) -> dict[str, Any]:
        if self.settings_repository is None or self.session is None:
            raise ValidationException(detail="System settings persistence is unavailable.", error_code="SETTINGS_UNAVAILABLE")

        actor_admin_id = await self._require_authorized_admin(payload)
        updates = payload.get("updates")
        if not isinstance(updates, dict) or not updates:
            raise ValidationException(detail="Settings updates are required.", error_code="SETTINGS_UPDATES_REQUIRED")

        category = str(payload.get("category") or "admin")
        persisted_items: list[SystemSettings] = []
        async with self.session.begin():
            for key, raw_value in updates.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValidationException(detail="Each setting key must be a non-empty string.", error_code="INVALID_SETTING_KEY")
                setting_key = key.strip()
                serialized_value = self._serialize_value(raw_value)
                existing = await self.settings_repository.get_by_key(setting_key)
                if existing is None:
                    created = await self.settings_repository.create_setting(
                        SystemSettings(
                            key=setting_key,
                            value=serialized_value,
                            value_type=self._infer_value_type(raw_value),
                            category=category,
                            description=f"System setting {setting_key}",
                            created_by=str(actor_admin_id),
                            updated_by=str(actor_admin_id),
                        )
                    )
                    persisted_items.append(created)
                else:
                    updated = await self.settings_repository.update_setting(existing.id, value=serialized_value, is_active=True)
                    if updated is None:
                        raise ValidationException(detail=f"Failed to update setting '{setting_key}'.", error_code="SETTING_UPDATE_FAILED")
                    updated.updated_by = str(actor_admin_id)
                    self.session.add(updated)
                    persisted_items.append(updated)

            await self._create_audit_log(
                actor_admin_id=actor_admin_id,
                action="admin.settings.update",
                description="System settings updated by administrator.",
                resource_type="system_settings",
                metadata={"keys": sorted(updates.keys())},
                new_value=updates,
            )

        return {
            "success": True,
            "data": {"items": [self._serialize_setting(item) for item in persisted_items]},
            "meta": {"source": "admin.settings", "updated_count": len(persisted_items)},
        }

    async def _require_authorized_admin(self, payload: dict[str, Any]) -> UUID:
        raw_admin_id = payload.get("admin_id")
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required for settings updates.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc

        if self.admin_repository is not None:
            admin = await self.admin_repository.get_by_id(admin_id)
            if admin is None or not bool(getattr(admin, "is_active", False)):
                raise AuthorizationException(detail="Admin is not authorized for settings updates.", error_code="ADMIN_NOT_AUTHORIZED")
        return admin_id

    def _serialize_setting(self, setting: SystemSettings) -> dict[str, Any]:
        return {
            "id": str(setting.id),
            "key": setting.key,
            "value": self._deserialize_value(setting.value),
            "value_type": setting.value_type,
            "category": setting.category,
            "description": setting.description,
            "is_active": setting.is_active,
            "created_by": setting.created_by,
            "updated_by": setting.updated_by,
            "created_at": setting.created_at.isoformat() if setting.created_at else None,
            "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
        }

    def _serialize_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple, bool, int, float)):
            return json.dumps(value, default=str)
        return str(value)

    def _deserialize_value(self, raw_value: str | None) -> Any:
        if raw_value is None:
            return None
        try:
            return json.loads(raw_value)
        except Exception:
            return raw_value

    def _infer_value_type(self, value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "float"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (list, tuple)):
            return "array"
        return "string"

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        action: str,
        description: str,
        resource_type: str,
        metadata: dict[str, Any],
        new_value: Any,
    ) -> None:
        if self.audit_repository is None:
            return
        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=str(actor_admin_id),
                action=action,
                category="admin_settings",
                description=description,
                resource_type=resource_type,
                metadata_payload=json.dumps(metadata, default=str),
                new_value=json.dumps(new_value, default=str),
            )
        )
