from __future__ import annotations

import logging
from typing import Any
from uuid import UUID


class UserAdministrationService:
    """Service for managing user accounts from the admin console."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def list_users(self, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": [], "meta": {"source": "admin.users"}}

    async def get_user(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": {"id": str(user_id)}, "meta": {"source": "admin.users"}}

    async def manage_user(self, *, user_id: UUID, action: str, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": {"id": str(user_id), "action": action}, "meta": {"source": "admin.users"}}
