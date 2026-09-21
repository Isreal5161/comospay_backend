from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.notification_repository import NotificationRepository
from app.services.notification_service import NotificationService
from app.utils.exceptions import AuthorizationException, ValidationException


class NotificationAdministrationService:
    """Service for broadcast notifications and admin messaging."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        notification_repository: NotificationRepository | None = None,
        notification_service: NotificationService | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.notification_repository = notification_repository
        self.notification_service = notification_service
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.session = session

    async def broadcast_notification(self, **payload: Any) -> dict[str, Any]:
        if self.notification_repository is None or self.notification_service is None or self.session is None:
            raise ValidationException(detail="Notification administration dependencies are unavailable.", error_code="NOTIFICATION_ADMIN_UNAVAILABLE")

        actor_admin_id = await self._require_authorized_admin(payload)
        message = payload.get("message")
        title = payload.get("title")
        channel = str(payload.get("channel") or "in_app")
        recipients_raw = payload.get("recipients")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        if not isinstance(message, str) or not message.strip():
            raise ValidationException(detail="Notification message is required.", error_code="NOTIFICATION_MESSAGE_REQUIRED")
        if recipients_raw is None:
            raise ValidationException(detail="At least one recipient is required.", error_code="NOTIFICATION_RECIPIENTS_REQUIRED")

        recipients: list[UUID] = []
        for value in recipients_raw if isinstance(recipients_raw, list) else [recipients_raw]:
            try:
                recipients.append(UUID(str(value)))
            except (ValueError, TypeError) as exc:
                raise ValidationException(detail="Recipient identifiers must be valid UUID values.", error_code="INVALID_RECIPIENT_ID") from exc

        dispatch_records: list[dict[str, Any]] = []

        if channel != "in_app":
            raise ValidationException(
                detail="Only in_app broadcast is supported until provider dispatch routing is configured.",
                error_code="UNSUPPORTED_BROADCAST_CHANNEL",
            )

        async with self.session.begin():
            for recipient_id in recipients:
                created = await self.notification_service.create_notification(
                    user_id=recipient_id,
                    title=title,
                    message=message.strip(),
                    notification_type="admin_broadcast",
                    category="admin",
                    reference=f"admin-broadcast:{actor_admin_id}",
                    metadata=metadata,
                    channel=channel,
                )
                dispatch_records.append(
                    {
                        "notification_id": created.get("id"),
                        "recipient_id": str(recipient_id),
                        "status": created.get("status") or "pending",
                        "channel": channel,
                        "delivery": {"status": "persisted", "channel": "in_app"},
                    }
                )

            await self._create_audit_log(
                actor_admin_id=actor_admin_id,
                action="admin.notification.broadcast",
                description="Admin broadcast notification dispatched.",
                metadata={"recipient_count": len(recipients), "channel": channel},
                new_value={"title": title, "message": message, "metadata": metadata},
            )

        return {
            "success": True,
            "data": {"notifications": dispatch_records, "count": len(dispatch_records)},
            "meta": {"source": "admin.notifications", "channel": channel},
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

    async def _create_audit_log(
        self,
        *,
        actor_admin_id: UUID,
        action: str,
        description: str,
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
                category="admin_notification",
                description=description,
                resource_type="notification",
                metadata_payload=json.dumps(metadata, default=str),
                new_value=json.dumps(new_value, default=str),
            )
        )

