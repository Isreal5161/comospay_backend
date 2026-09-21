from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.user_repository import UserRepository
from app.services.account_state import UserAccountStateService
from app.services.kyc_helpers import get_user_or_raise
from app.services.notification_service import NotificationService
from app.services.security.account_lockout import AccountLockoutService
from app.utils.exceptions import AuthorizationException, ValidationException


class UserAdministrationService:
    """Service for managing user accounts from the admin console."""

    _LOCK_ACTIONS = {"lock", "unlock", "lock_account", "unlock_account"}

    def __init__(
        self,
        logger: logging.Logger | None = None,
        user_repository: UserRepository | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        notification_service: NotificationService | None = None,
        account_lockout_service: AccountLockoutService | None = None,
        account_state_service: UserAccountStateService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.user_repository = user_repository
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.notification_service = notification_service
        self.account_lockout_service = account_lockout_service
        self.account_state_service = account_state_service
        self.session = session

    async def list_users(self, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.user_repository is None:
            raise ValidationException(detail="User repository is unavailable.", error_code="USER_ADMIN_UNAVAILABLE")

        page = int(payload.get("page") or 1)
        page_size = int(payload.get("page_size") or 20)
        status = payload.get("status")
        order_by = str(payload.get("order_by") or "created_at")
        descending = bool(payload.get("descending", True))

        users, total = await self.user_repository.get_all_users(
            page=page,
            page_size=page_size,
            status=str(status) if status is not None else None,
            order_by=order_by,
            descending=descending,
        )
        return {
            "success": True,
            "data": {
                "items": [self._serialize_user(user) for user in users],
                "total": total,
            },
            "meta": {"source": "admin.users", "page": page, "page_size": page_size},
        }

    async def get_user(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_user_repository()
        await self._require_authorized_admin(payload)
        user = await self._get_user_or_raise(user_id=user_id)
        return {"success": True, "data": self._serialize_user(user), "meta": {"source": "admin.users"}}

    async def manage_user(self, *, user_id: UUID, action: str, **payload: Any) -> dict[str, Any]:
        self._require_user_repository()
        self._require_account_state_service()
        actor_admin_id = await self._require_authorized_admin(payload)

        normalized_action = self._normalize_action(action)
        reason = self._optional_text(payload.get("reason"))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        if normalized_action in {"lock", "unlock"}:
            result = await self._handle_lock_action(
                user_id=user_id,
                action=normalized_action,
                reason=reason,
                metadata=metadata,
            )
            user = await self._get_user_or_raise(user_id=user_id)
        else:
            user = await self._get_user_or_raise(user_id=user_id)
            old_payload = self._serialize_user(user)
            updates = self.account_state_service.resolve_state_updates(action=normalized_action)
            updates["metadata_payload"] = self._merge_management_metadata(
                current=user.metadata_payload,
                action=normalized_action,
                actor_admin_id=actor_admin_id,
                reason=reason,
                metadata=metadata,
            )

            async with self._session_scope():
                user = await self.user_repository.update_user(user, **updates)
            result = {
                "action": normalized_action,
                "state": {key: updates[key] for key in updates if key != "metadata_payload"},
                "before": old_payload,
            }

        await self._create_audit_log(
            actor_admin_id=actor_admin_id,
            user=user,
            action=normalized_action,
            reason=reason,
            metadata=metadata,
            result=result,
        )
        await self._notify_user(user=user, action=normalized_action, reason=reason)

        return {
            "success": True,
            "data": {
                "user": self._serialize_user(user),
                "action": normalized_action,
                "result": result,
            },
            "meta": {"source": "admin.users"},
        }

    async def _handle_lock_action(
        self,
        *,
        user_id: UUID,
        action: str,
        reason: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if self.account_lockout_service is None:
            raise ValidationException(detail="Account lockout service is unavailable.", error_code="ACCOUNT_LOCKOUT_UNAVAILABLE")

        if action == "lock":
            duration = metadata.get("duration_minutes")
            permanent = bool(metadata.get("permanent", False))
            if permanent:
                return await self.account_lockout_service.permanent_lock(
                    user_id=user_id,
                    reason=reason,
                    source="admin_users",
                )
            return await self.account_lockout_service.temporary_lock(
                user_id=user_id,
                reason=reason,
                duration_minutes=int(duration) if duration is not None else None,
                source="admin_users",
            )

        return await self.account_lockout_service.unlock_account(
            user_id=user_id,
            reason=reason,
            by_admin=True,
            source="admin_users",
        )

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

    def _normalize_action(self, action: str) -> str:
        self._require_account_state_service()
        normalized = (action or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in self._LOCK_ACTIONS:
            return "unlock" if "unlock" in normalized else "lock"
        return self.account_state_service.normalize_transition_action(normalized)

    def _require_user_repository(self) -> None:
        if self.user_repository is None:
            raise ValidationException(detail="User administration dependencies are unavailable.", error_code="USER_ADMIN_UNAVAILABLE")

    def _require_account_state_service(self) -> None:
        if self.account_state_service is None:
            raise ValidationException(detail="User account state service dependency is unavailable.", error_code="ACCOUNT_STATE_UNAVAILABLE")

    async def _get_user_or_raise(self, *, user_id: UUID) -> User:
        if self.user_repository is None:
            raise ValidationException(detail="User administration dependencies are unavailable.", error_code="USER_ADMIN_UNAVAILABLE")
        return await get_user_or_raise(user_repository=self.user_repository, user_id=user_id)

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        user: User,
        action: str,
        reason: str | None,
        metadata: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if self.audit_repository is None:
            return
        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=str(actor_admin_id),
                action=f"admin.user.{action}",
                category="admin_user",
                description=f"Administrator performed '{action}' on user.",
                resource_type="user",
                resource_id=str(user.id),
                metadata_payload=json.dumps({"reason": reason, "metadata": metadata}, default=str),
                new_value=json.dumps(result, default=str),
            )
        )

    async def _notify_user(self, *, user: User, action: str, reason: str | None) -> None:
        if self.notification_service is None:
            return
        await self.notification_service.create_notification(
            user_id=user.id,
            title="Account Update",
            message=self._build_notification_message(action=action, reason=reason),
            notification_type="admin_user_management",
            category="account",
            reference=f"admin-user:{user.id}:{action}",
            metadata={"action": action, "reason": reason},
            channel="in_app",
        )

    def _build_notification_message(self, *, action: str, reason: str | None) -> str:
        base_messages = {
            "activate": "Your account has been activated.",
            "deactivate": "Your account has been deactivated.",
            "suspend": "Your account has been suspended.",
            "unsuspend": "Your account suspension has been lifted.",
            "block": "Your account has been blocked.",
            "unblock": "Your account has been unblocked.",
            "lock": "Your account has been locked for security reasons.",
            "unlock": "Your account lock has been removed.",
            "verify_email": "Your email has been marked as verified.",
            "unverify_email": "Your email verification has been removed.",
            "verify_phone": "Your phone number has been marked as verified.",
            "unverify_phone": "Your phone verification has been removed.",
        }
        message = base_messages.get(action, "Your account has been updated by an administrator.")
        if reason:
            return f"{message} Reason: {reason}"
        return message

    def _merge_management_metadata(
        self,
        *,
        current: str | None,
        action: str,
        actor_admin_id: UUID,
        reason: str | None,
        metadata: dict[str, Any],
    ) -> str | None:
        payload = self._parse_metadata_payload(current)
        payload["last_admin_action"] = {
            "action": action,
            "admin_id": str(actor_admin_id),
            "reason": reason,
            "metadata": metadata,
            "at": datetime.utcnow().isoformat(),
        }
        return json.dumps(payload, default=str)

    def _parse_metadata_payload(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"value": value}

    def _serialize_user(self, user: User) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "username": user.username,
            "status": user.status,
            "is_active": user.is_active,
            "is_blocked": user.is_blocked,
            "is_suspended": user.is_suspended,
            "email_verified": user.email_verified,
            "phone_verified": user.phone_verified,
            "account_locked_until": user.account_locked_until.isoformat() if user.account_locked_until else None,
            "failed_login_attempts": user.failed_login_attempts,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        stripped = value.strip()
        return stripped or None

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        if self.session.in_transaction():
            return _ActiveSessionContext(self.session)
        return self.session.begin()


class _ActiveSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> None:
        if not self.session.in_transaction():
            raise RuntimeError("Expected active transaction for admin user operation.")
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
