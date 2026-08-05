from __future__ import annotations

import logging
from typing import Any

from app.services.notification_service import NotificationService
from app.utils.exceptions import ValidationException


class NotificationWebhookService:
    """Handle notification-provider delivery and status webhook callbacks."""

    def __init__(self, *, notification_service: NotificationService | None = None, logger: logging.Logger | None = None) -> None:
        self.notification_service = notification_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_sms_delivery_receipt(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_notification_service()
        return await self.notification_service.create_notification(
            user_id=self._user_id(payload),
            title="SMS delivery receipt",
            message=self._message(payload),
            notification_type="info",
            category="notification",
            reference=self._reference(payload),
            metadata={"provider": self._provider_name(payload), "status": self._status(payload)},
        )

    async def process_email_delivery_event(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_notification_service()
        return await self.notification_service.create_notification(
            user_id=self._user_id(payload),
            title="Email delivery event",
            message=self._message(payload),
            notification_type="info",
            category="notification",
            reference=self._reference(payload),
            metadata={"provider": self._provider_name(payload), "status": self._status(payload)},
        )

    async def process_push_notification_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_notification_service()
        return await self.notification_service.create_notification(
            user_id=self._user_id(payload),
            title="Push notification callback",
            message=self._message(payload),
            notification_type="info",
            category="notification",
            reference=self._reference(payload),
            metadata={"provider": self._provider_name(payload), "status": self._status(payload)},
        )

    async def process_status_update(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_notification_service()
        return await self.notification_service.create_notification(
            user_id=self._user_id(payload),
            title="Notification status update",
            message=self._message(payload),
            notification_type="info",
            category="notification",
            reference=self._reference(payload),
            metadata={"provider": self._provider_name(payload), "status": self._status(payload)},
        )

    def _require_notification_service(self) -> NotificationService:
        if self.notification_service is None:
            raise ValidationException("NotificationService dependency is required for notification webhook processing.")
        return self.notification_service

    def _user_id(self, payload: dict[str, Any]) -> str | None:
        return payload.get("user_id") or payload.get("recipient_id") or payload.get("data", {}).get("user_id")

    def _reference(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("reference") or payload.get("event_id") or payload.get("data", {}).get("reference") or "") or None

    def _message(self, payload: dict[str, Any]) -> str:
        return str(payload.get("message") or payload.get("description") or "Notification webhook received")

    def _status(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("status") or payload.get("event_status") or payload.get("data", {}).get("status") or "") or None

    def _provider_name(self, payload: dict[str, Any]) -> str:
        return str(payload.get("provider_name") or payload.get("provider") or "notification")
