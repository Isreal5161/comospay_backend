from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.provider_repository import ProviderRepository
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.utils.exceptions import AuthorizationException, ValidationException


class ProviderAdministrationService:
    """Service for managing providers and integrations."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        provider_repository: ProviderRepository | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        provider_health_service: ProviderHealthService | None = None,
        provider_selector: ProviderSelector | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.provider_repository = provider_repository
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.provider_health_service = provider_health_service
        self.provider_selector = provider_selector
        self.session = session

    async def list_providers(self, **payload: Any) -> dict[str, Any]:
        if self.provider_repository is None:
            raise ValidationException(detail="Provider persistence is unavailable.", error_code="PROVIDER_UNAVAILABLE")

        page = payload.get("page")
        page_size = payload.get("page_size")
        category = payload.get("category")
        status = payload.get("status")
        providers, total = await self.provider_repository.get_all_providers(
            page=page,
            page_size=page_size,
            category=category,
            status=status,
        )
        return {
            "success": True,
            "data": {"items": [self._serialize_provider(item) for item in providers], "total": total},
            "meta": {"source": "admin.providers", "page": page, "page_size": page_size},
        }

    async def configure_provider(self, *, provider_id: str, **payload: Any) -> dict[str, Any]:
        if self.provider_repository is None or self.session is None:
            raise ValidationException(detail="Provider persistence is unavailable.", error_code="PROVIDER_UNAVAILABLE")

        actor_admin_id = await self._require_authorized_admin(payload)
        action = str(payload.get("action") or "update").strip().lower()
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}

        try:
            provider_uuid = UUID(str(provider_id))
        except (ValueError, TypeError) as exc:
            raise ValidationException(detail="Provider identifier is invalid.", error_code="INVALID_PROVIDER_ID") from exc

        provider = await self.provider_repository.get_provider_by_id(provider_uuid)
        if provider is None:
            raise ValidationException(detail="Provider not found.", error_code="PROVIDER_NOT_FOUND")

        old_payload = self._serialize_provider(provider)
        update_fields = self._extract_provider_updates(config)
        priority = update_fields.pop("priority", None)
        state_updates = self._extract_provider_state_updates(action=action, config=update_fields)

        async with self.session.begin():
            updated = provider
            updated = await self._apply_provider_configuration(provider=updated, update_fields=update_fields)

            if priority is not None:
                if self.provider_selector is None:
                    raise ValidationException(detail="Provider selector is unavailable.", error_code="PROVIDER_SELECTOR_UNAVAILABLE")
                updated = await self.provider_selector.update_provider_priority(provider_id=updated.id, priority=int(priority))

            if state_updates:
                if self.provider_health_service is None:
                    raise ValidationException(detail="Provider health service is unavailable.", error_code="PROVIDER_HEALTH_UNAVAILABLE")
                updated = await self.provider_health_service.update_provider_state(provider_id=updated.id, **state_updates)

            if self.provider_selector is not None:
                await self.provider_selector.invalidate_provider_cache(category=provider.category)
                if updated.category != provider.category:
                    await self.provider_selector.invalidate_provider_cache(category=updated.category)

            if updated is provider and priority is None and not state_updates and not update_fields:
                raise ValidationException(detail="No provider configuration updates were supplied.", error_code="PROVIDER_UPDATE_REQUIRED")

            await self._create_audit_log(
                actor_admin_id=actor_admin_id,
                action="admin.provider.configure",
                description="Provider configuration updated by administrator.",
                resource_id=str(updated.id),
                old_value=old_payload,
                new_value=self._serialize_provider(updated),
            )

        return {
            "success": True,
            "data": self._serialize_provider(updated),
            "meta": {"source": "admin.providers", "action": action},
        }

    async def _require_authorized_admin(self, payload: dict[str, Any]) -> UUID:
        raw_admin_id = payload.get("admin_id")
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc

        if self.admin_repository is not None:
            admin = await self.admin_repository.get_by_id(admin_id)
            if admin is None or not bool(getattr(admin, "is_active", False)):
                raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")
        return admin_id

    def _serialize_provider(self, provider: Any) -> dict[str, Any]:
        return {
            "id": str(provider.id),
            "code": provider.code,
            "name": provider.name,
            "category": provider.category,
            "description": provider.description,
            "status": provider.status,
            "is_active": provider.is_active,
            "environment": provider.environment,
            "priority": provider.priority,
            "base_url": provider.base_url,
            "api_version": provider.api_version,
            "credential_ref": provider.credential_ref,
            "health_status": provider.health_status,
            "metadata_payload": provider.metadata_payload,
            "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
        }

    def _extract_provider_updates(self, config: dict[str, Any]) -> dict[str, Any]:
        allowed_fields = {
            "name",
            "code",
            "category",
            "description",
            "environment",
            "priority",
            "base_url",
            "api_version",
            "credential_ref",
            "metadata_payload",
        }
        return {key: value for key, value in config.items() if key in allowed_fields}

    def _extract_provider_state_updates(self, *, action: str, config: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if action == "enable":
            updates["is_active"] = True
        elif action == "disable":
            updates["is_active"] = False

        for field in ("is_active", "status", "health_status"):
            if field in config:
                updates[field] = config.pop(field)
        return updates

    async def _apply_provider_configuration(
        self,
        *,
        provider: Any,
        update_fields: dict[str, Any],
    ) -> Any:
        if self.provider_repository is None:
            raise ValidationException(detail="Provider persistence is unavailable.", error_code="PROVIDER_UNAVAILABLE")

        if not update_fields:
            return provider
        return await self.provider_repository.update_provider(provider, **update_fields)

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        action: str,
        description: str,
        resource_id: str,
        old_value: dict[str, Any],
        new_value: dict[str, Any],
    ) -> None:
        if self.audit_repository is None:
            return
        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=str(actor_admin_id),
                action=action,
                category="admin_provider",
                description=description,
                resource_type="provider",
                resource_id=resource_id,
                old_value=json.dumps(old_value, default=str),
                new_value=json.dumps(new_value, default=str),
            )
        )
