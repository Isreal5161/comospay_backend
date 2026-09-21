from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.services.auth.password_service import PasswordService
from app.utils.exceptions import AuthorizationException, ValidationException


class StaffAdministrationService:
    """Service for admin staff management."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        password_service: PasswordService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.password_service = password_service
        self.session = session

    async def _list_staff(self, **payload: Any) -> dict[str, Any]:
        if self.admin_repository is None:
            raise ValidationException(detail="Admin persistence is unavailable.", error_code="ADMIN_PERSISTENCE_UNAVAILABLE")

        page = int(payload.get("page") or 1)
        page_size = int(payload.get("page_size") or 20)
        admins, total = await self.admin_repository.get_all(page=page, page_size=page_size)
        return {
            "success": True,
            "data": {"items": [self._serialize_admin(admin) for admin in admins], "total": total},
            "meta": {"source": "admin.staff", "page": page, "page_size": page_size},
        }

    async def create_admin_account(self, **payload: Any) -> dict[str, Any]:
        if self.admin_repository is None or self.session is None:
            raise ValidationException(detail="Admin persistence is unavailable.", error_code="ADMIN_PERSISTENCE_UNAVAILABLE")
        if self.password_service is None:
            raise ValidationException(detail="Password service is unavailable.", error_code="PASSWORD_SERVICE_UNAVAILABLE")

        actor_admin_id = await self._require_authorized_admin(payload.get("admin_id"))

        email = payload.get("email")
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(email, str) or not email.strip():
            raise ValidationException(detail="Admin email is required.", error_code="ADMIN_EMAIL_REQUIRED")
        if not isinstance(username, str) or not username.strip():
            raise ValidationException(detail="Admin username is required.", error_code="ADMIN_USERNAME_REQUIRED")
        if not isinstance(password, str) or not password:
            raise ValidationException(detail="Admin password is required.", error_code="ADMIN_PASSWORD_INVALID")

        await self.password_service.enforce_password_policy(password=password)

        existing = await self.admin_repository.get_by_email(email.strip().lower())
        if existing is not None:
            raise ValidationException(detail="Admin email already exists.", error_code="ADMIN_EMAIL_EXISTS")

        new_admin = Admin(
            email=email.strip().lower(),
            username=username.strip(),
            first_name=payload.get("first_name"),
            last_name=payload.get("last_name"),
            phone=payload.get("phone"),
            role=str(payload.get("role") or "admin"),
            status="active",
            is_active=True,
            password_hash=self.password_service.hash_password(password),
        )

        async with self.session.begin():
            created = await self.admin_repository.create(new_admin)
            await self._create_audit_log(
                actor_admin_id=actor_admin_id,
                action="admin.staff.create",
                description="Administrator created a new admin account.",
                resource_id=str(created.id),
                new_value=self._serialize_admin(created),
            )

        return {"success": True, "data": self._serialize_admin(created), "meta": {"source": "admin.staff"}}

    async def update_admin_account(self, *, admin_id: str, **payload: Any) -> dict[str, Any]:
        if self.admin_repository is None or self.session is None:
            raise ValidationException(detail="Admin persistence is unavailable.", error_code="ADMIN_PERSISTENCE_UNAVAILABLE")

        actor_admin_id = await self._require_authorized_admin(payload.get("performed_by_admin_id") or payload.get("admin_id"))
        try:
            target_admin_id = UUID(str(admin_id))
        except (ValueError, TypeError) as exc:
            raise ValidationException(detail="Admin identifier is invalid.", error_code="INVALID_ADMIN_ID") from exc

        target_admin = await self.admin_repository.get_by_id(target_admin_id)
        if target_admin is None:
            raise ValidationException(detail="Admin account not found.", error_code="ADMIN_NOT_FOUND")

        update_fields = {
            "email": payload.get("email"),
            "username": payload.get("username"),
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
            "phone": payload.get("phone"),
            "role": payload.get("role"),
            "status": payload.get("status"),
            "is_active": payload.get("is_active"),
        }
        update_fields = {k: v for k, v in update_fields.items() if v is not None}
        if not update_fields:
            raise ValidationException(detail="No admin updates were supplied.", error_code="ADMIN_UPDATES_REQUIRED")

        old_value = self._serialize_admin(target_admin)
        async with self.session.begin():
            updated = await self.admin_repository.update(target_admin, **update_fields)
            await self._create_audit_log(
                actor_admin_id=actor_admin_id,
                action="admin.staff.update",
                description="Administrator updated an admin account.",
                resource_id=str(updated.id),
                old_value=old_value,
                new_value=self._serialize_admin(updated),
            )

        return {"success": True, "data": self._serialize_admin(updated), "meta": {"source": "admin.staff"}}

    async def _require_authorized_admin(self, raw_admin_id: Any) -> UUID:
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc

        if self.admin_repository is not None:
            actor_admin = await self.admin_repository.get_by_id(admin_id)
            if actor_admin is None or not bool(getattr(actor_admin, "is_active", False)):
                raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")
        return admin_id

    def _serialize_admin(self, admin: Admin) -> dict[str, Any]:
        return {
            "id": str(admin.id),
            "email": admin.email,
            "username": admin.username,
            "first_name": admin.first_name,
            "last_name": admin.last_name,
            "phone": admin.phone,
            "role": admin.role,
            "status": admin.status,
            "is_active": admin.is_active,
            "is_super_admin": admin.is_super_admin,
            "created_at": admin.created_at.isoformat() if admin.created_at else None,
            "updated_at": admin.updated_at.isoformat() if admin.updated_at else None,
        }

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        action: str,
        description: str,
        resource_id: str,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any],
    ) -> None:
        if self.audit_repository is None:
            return
        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=str(actor_admin_id),
                action=action,
                category="admin_staff",
                description=description,
                resource_type="admin",
                resource_id=resource_id,
                old_value=json.dumps(old_value, default=str) if old_value is not None else None,
                new_value=json.dumps(new_value, default=str),
            )
        )
