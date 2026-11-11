from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.kyc import KYC
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.kyc_repository import KYCRepository
from app.services.admin.authorization import require_active_admin
from app.services.user.kyc import KYCService as UserKYCService
from app.services.notification_service import NotificationService
from app.utils.exceptions import AuthorizationException, ValidationException


class KYCService:
    """Service for reviewing and managing user KYC approvals."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        kyc_domain_service: UserKYCService | None = None,
        kyc_repository: KYCRepository | None = None,
        admin_repository: AdminRepository | None = None,
        notification_service: NotificationService | None = None,
        audit_repository: AuditLogRepository | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.kyc_domain_service = kyc_domain_service
        self.kyc_repository = kyc_repository
        self.admin_repository = admin_repository
        self.notification_service = notification_service
        self.audit_repository = audit_repository
        self.session = session

    async def review_kyc(self, *, user_id: UUID, kyc_id: UUID, action: str, **payload: Any) -> dict[str, Any]:
        review_actor = await self._require_authorized_admin(payload)
        review_notes = self._to_optional_str(payload.get("review_notes"))
        rejection_reason = self._resolve_rejection_reason(payload=payload)
        updated = await self._review_with_domain_service(
            user_id=user_id,
            kyc_id=kyc_id,
            action=action,
            review_actor=review_actor,
            review_notes=review_notes,
            rejection_reason=rejection_reason,
        )
        normalized_action = updated.verification_status

        try:
            async with self._session_scope():
                await self._create_audit_log(
                    actor_id=review_actor,
                    decision=normalized_action,
                    user_id=user_id,
                    record=updated,
                    review_notes=review_notes,
                    rejection_reason=rejection_reason,
                )
                await self._notify_user(
                    user_id=user_id,
                    decision=normalized_action,
                    kyc_id=updated.id,
                    review_notes=review_notes,
                    rejection_reason=rejection_reason,
                )
            if self.session is not None:
                await self.session.commit()
        except Exception:
            if self.session is not None:
                await self.session.rollback()
            raise

        return self._serialize_kyc(updated)

    async def get_user_kyc(self, *, user_id: UUID, page: int = 1, page_size: int = 20, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.kyc_repository is None or self.kyc_domain_service is None:
            raise ValidationException(detail="KYC administration dependencies are unavailable.", error_code="ADMIN_KYC_UNAVAILABLE")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValidationException(detail="Invalid KYC pagination.", error_code="INVALID_PAGINATION")

        await self.kyc_domain_service.get_kyc_status(user_id=user_id)
        records, total = await self.kyc_repository.get_user_kyc(
            user_id=user_id,
            page=page,
            page_size=page_size,
            order_by="created_at",
            descending=True,
        )
        return {
            "items": [self._serialize_kyc_profile(record) for record in records],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def list_kyc(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
        document_type: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.kyc_repository is None:
            raise ValidationException(detail="KYC administration dependencies are unavailable.", error_code="ADMIN_KYC_UNAVAILABLE")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValidationException(detail="Invalid KYC pagination.", error_code="INVALID_PAGINATION")

        records, total = await self.kyc_repository.get_admin_kyc(
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            document_type=document_type,
        )
        items = []
        for record, user_fields in records:
            item = self._serialize_kyc_profile(record)
            item["updated_at"] = record.updated_at.isoformat() if record.updated_at else None
            item.update(
                {
                    "user_id": str(record.user_id),
                    **user_fields,
                }
            )
            items.append(item)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    def _require_dependencies(self) -> None:
        if self.kyc_domain_service is None:
            raise ValidationException(detail="KYC administration dependencies are unavailable.", error_code="ADMIN_KYC_UNAVAILABLE")

    async def _review_with_domain_service(
        self,
        *,
        user_id: UUID,
        kyc_id: UUID,
        action: str,
        review_actor: str,
        review_notes: str | None,
        rejection_reason: str | None,
    ) -> KYC:
        self._require_dependencies()
        assert self.kyc_domain_service is not None
        return await self.kyc_domain_service.review_kyc(
            user_id=user_id,
            kyc_id=kyc_id,
            action=action,
            reviewed_by=review_actor,
            review_notes=review_notes,
            rejection_reason=rejection_reason,
        )

    def _resolve_rejection_reason(self, *, payload: dict[str, Any]) -> str | None:
        raw = payload.get("rejection_reason")
        if raw is None:
            raw = payload.get("reason")
        if raw is None:
            raw = payload.get("review_notes")
        return self._to_optional_str(raw)

    async def _require_authorized_admin(self, payload: dict[str, Any]) -> str:
        raw_admin_id = payload.get("admin_id")
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc
        await require_active_admin(self.admin_repository, admin_id)
        return str(admin_id)

    async def _create_audit_log(
        self,
        *,
        actor_id: str,
        decision: str,
        user_id: UUID,
        record: KYC,
        review_notes: str | None,
        rejection_reason: str | None,
    ) -> None:
        if self.audit_repository is None:
            return

        await self.audit_repository.create_audit_log(
            AuditLog(
                actor_type="admin",
                actor_id=actor_id,
                action=f"admin.kyc.{decision}",
                category="admin_kyc",
                description=f"Admin {decision} KYC for user {user_id}.",
                resource_type="kyc",
                resource_id=str(record.id),
                new_value=json.dumps(
                    {
                        "kyc_id": str(record.id),
                        "user_id": str(user_id),
                        "verification_status": record.verification_status,
                        "reviewed_by": record.reviewed_by,
                        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
                        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
                        "rejected_at": record.rejected_at.isoformat() if record.rejected_at else None,
                        "rejection_reason": record.rejection_reason,
                        "review_notes": review_notes,
                        "effective_rejection_reason": rejection_reason,
                    },
                    default=str,
                ),
            )
        )

    async def _notify_user(
        self,
        *,
        user_id: UUID,
        decision: str,
        kyc_id: UUID,
        review_notes: str | None,
        rejection_reason: str | None,
    ) -> None:
        if self.notification_service is None or not hasattr(self.notification_service, "create_notification"):
            return

        message = "Your KYC verification has been approved."
        if decision == "rejected":
            suffix = rejection_reason or review_notes or "Please review the submitted information and try again."
            message = f"Your KYC verification was rejected. {suffix}"

        await self.notification_service.create_notification(
            user_id=user_id,
            title="KYC Review Update",
            message=message,
            notification_type="kyc_review",
            category="kyc",
            reference=f"admin-kyc-review:{kyc_id}",
            metadata={
                "decision": decision,
                "kyc_id": str(kyc_id),
                "review_notes": review_notes,
                "rejection_reason": rejection_reason,
            },
            channel="in_app",
        )

    def _serialize_kyc(self, record: KYC) -> dict[str, Any]:
        return {
            "id": str(record.id),
            "user_id": str(record.user_id),
            "verification_status": record.verification_status,
            "verification_level": record.verification_level,
            "document_type": record.document_type,
            "document_verification_status": record.document_verification_status,
            "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
            "reviewed_by": record.reviewed_by,
            "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
            "approved_at": record.approved_at.isoformat() if record.approved_at else None,
            "rejected_at": record.rejected_at.isoformat() if record.rejected_at else None,
            "rejection_reason": record.rejection_reason,
            "is_active": record.is_active,
            "is_verified": record.is_verified,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def _serialize_kyc_profile(self, record: KYC) -> dict[str, Any]:
        return {
            "id": str(record.id),
            "verification_status": record.verification_status,
            "verification_level": record.verification_level,
            "document_type": record.document_type,
            "document_verification_status": record.document_verification_status,
            "submitted_at": record.submitted_at.isoformat() if record.submitted_at else None,
            "reviewed_by": record.reviewed_by,
            "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
            "approved_at": record.approved_at.isoformat() if record.approved_at else None,
            "rejected_at": record.rejected_at.isoformat() if record.rejected_at else None,
            "rejection_reason": record.rejection_reason,
            "is_active": record.is_active,
            "is_verified": record.is_verified,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    def _to_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        trimmed = value.strip()
        return trimmed or None

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
            raise RuntimeError("Expected active transaction for KYC admin operation.")
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
