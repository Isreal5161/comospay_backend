from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.services.auth.session_service import SessionService
from app.utils.exceptions import AuthorizationException, ValidationException


class SessionAdministrationService:
    """Administrative session operations for user session management."""

    def __init__(self, logger: logging.Logger | None = None, session_service: SessionService | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.session_service = session_service or SessionService(logger=self.logger)

    async def revoke_session_by_id(self, *, session_id: str, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        """Revoke a specific session for a user from the admin console."""
        if not session_id:
            raise ValidationException(detail="Session identifier is required.", error_code="SESSION_ID_REQUIRED")

        if not isinstance(reason, str) or not reason.strip():
            reason = "admin_revoke"

        revoked_payload = await self.session_service.revoke_session(session_id=session_id, reason=reason)
        return {
            "success": True,
            "data": {"session_id": revoked_payload.get("session_id"), "status": revoked_payload.get("status"), "reason": reason},
            "meta": {"source": "admin.sessions", "action": "revoke_session"},
        }

    async def get_user_sessions(self, *, user_id: str | UUID, **payload: Any) -> dict[str, Any]:
        """List active sessions for a specific user."""
        user_identifier = str(user_id)
        if not user_identifier:
            raise ValidationException(detail="User identifier is required.", error_code="USER_ID_REQUIRED")

        sessions = await self.session_service.get_active_sessions(user_id=user_identifier)
        return {
            "success": True,
            "data": sessions,
            "meta": {"source": "admin.sessions", "action": "list_user_sessions", "user_id": user_identifier},
        }

    async def revoke_all_user_sessions(self, *, user_id: str | UUID, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        """Revoke every active session for a specific user."""
        user_identifier = str(user_id)
        if not user_identifier:
            raise ValidationException(detail="User identifier is required.", error_code="USER_ID_REQUIRED")

        if not isinstance(reason, str) or not reason.strip():
            reason = "admin_revoke"

        revoked_count = await self.session_service.revoke_all_user_sessions(user_id=user_identifier, reason=reason)
        return {
            "success": True,
            "data": {"user_id": user_identifier, "revoked_count": revoked_count, "reason": reason},
            "meta": {"source": "admin.sessions", "action": "revoke_all_user_sessions"},
        }

    async def get_active_session_count(self, *, user_id: str | UUID, **payload: Any) -> dict[str, Any]:
        """Return the number of active sessions for a specific user."""
        user_identifier = str(user_id)
        if not user_identifier:
            raise ValidationException(detail="User identifier is required.", error_code="USER_ID_REQUIRED")

        sessions = await self.session_service.get_active_sessions(user_id=user_identifier)
        return {
            "success": True,
            "data": {"user_id": user_identifier, "count": len(sessions)},
            "meta": {"source": "admin.sessions", "action": "get_active_session_count"},
        }
