from __future__ import annotations

from typing import Any
from uuid import UUID

from app.config.settings import settings
from app.utils.exceptions import AuthorizationException


async def require_active_admin(admin_repository: Any, admin_id: UUID) -> None:
    if admin_repository is None:
        raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")

    admin = await admin_repository.get_by_id(admin_id)
    if (
        admin is None
        or not bool(getattr(admin, "is_active", False))
        or str(getattr(admin, "status", "") or "").strip().lower() != "active"
    ):
        raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")

    configured_roles = getattr(settings, "admin_allowed_roles", None)
    if isinstance(configured_roles, str):
        configured_roles = [item.strip() for item in configured_roles.split(",") if item.strip()]
    allowed_roles = configured_roles or ["super_admin", "admin"]
    role = str(getattr(admin, "role", "") or "").strip().lower().replace(" ", "_")
    if role not in {str(value).strip().lower().replace(" ", "_") for value in allowed_roles}:
        raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")
