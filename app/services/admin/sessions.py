from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.models.audit_log import AuditLog
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.services.auth.session_service import SessionService
from app.services.auth.session_service import SessionNotFoundException, SessionRevokedException
from app.utils.exceptions import AuthorizationException, ValidationException


class SessionAdministrationService:
    """Administrative session operations for user session management."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        session_service: SessionService | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.session_service = session_service
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository

    async def revoke_session_by_id(self, *, session_id: str, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        """Revoke a specific session for a user from the admin console."""
        self._require_session_service()
        actor_admin_id = await self._require_authorized_admin(payload)
        if not session_id:
            raise ValidationException(detail="Session identifier is required.", error_code="SESSION_ID_REQUIRED")

        if not isinstance(reason, str) or not reason.strip():
            reason = "admin_revoke"

        revoked_payload: dict[str, Any] = {"session_id": session_id, "status": "revoked"}
        idempotent = False
        try:
            revoked_payload = await self.session_service.revoke_session(session_id=session_id, reason=reason)
        except (SessionNotFoundException, SessionRevokedException):
            idempotent = True

        await self._create_audit_log(
            actor_admin_id=actor_admin_id,
            action="admin.session.revoke",
            description="Administrator revoked a user session.",
            resource_id=session_id,
            metadata={"reason": reason, "idempotent": idempotent},
            new_value={"status": "revoked"},
        )
        return {
            "success": True,
            "data": {
                "session_id": revoked_payload.get("session_id", session_id),
                "status": revoked_payload.get("status", "revoked"),
                "reason": reason,
                "idempotent": idempotent,
            },
            "meta": {"source": "admin.sessions", "action": "revoke_session"},
        }

    async def get_user_sessions(self, *, user_id: str | UUID, **payload: Any) -> dict[str, Any]:
        """List active sessions for a specific user."""
        self._require_session_service()
        await self._require_authorized_admin(payload)
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
        self._require_session_service()
        actor_admin_id = await self._require_authorized_admin(payload)
        user_identifier = str(user_id)
        if not user_identifier:
            raise ValidationException(detail="User identifier is required.", error_code="USER_ID_REQUIRED")

        if not isinstance(reason, str) or not reason.strip():
            reason = "admin_revoke"

        revoked_count = await self.session_service.revoke_all_user_sessions(user_id=user_identifier, reason=reason)
        await self._create_audit_log(
            actor_admin_id=actor_admin_id,
            action="admin.session.revoke_all",
            description="Administrator revoked all user sessions.",
            resource_id=user_identifier,
            metadata={"reason": reason},
            new_value={"revoked_count": revoked_count},
        )
        return {
            "success": True,
            "data": {"user_id": user_identifier, "revoked_count": revoked_count, "reason": reason},
            "meta": {"source": "admin.sessions", "action": "revoke_all_user_sessions"},
        }

    async def get_active_session_count(self, *, user_id: str | UUID, **payload: Any) -> dict[str, Any]:
        """Return the number of active sessions for a specific user."""
        self._require_session_service()
        await self._require_authorized_admin(payload)
        user_identifier = str(user_id)
        if not user_identifier:
            raise ValidationException(detail="User identifier is required.", error_code="USER_ID_REQUIRED")

        sessions = await self.session_service.get_active_sessions(user_id=user_identifier)
        return {
            "success": True,
            "data": {"user_id": user_identifier, "count": len(sessions)},
            "meta": {"source": "admin.sessions", "action": "get_active_session_count"},
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

    def _require_session_service(self) -> None:
        if self.session_service is None:
            raise ValidationException(detail="Session administration dependencies are unavailable.", error_code="SESSION_ADMIN_UNAVAILABLE")

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        action: str,
        description: str,
        resource_id: str,
        metadata: dict[str, Any],
        new_value: dict[str, Any],
    ) -> None:
        if self.audit_repository is None:
            return
        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=str(actor_admin_id),
                action=action,
                category="admin_session",
                description=description,
                resource_type="session",
                resource_id=resource_id,
                metadata_payload=json.dumps(metadata, default=str),
                new_value=json.dumps(new_value, default=str),
            )
        )
