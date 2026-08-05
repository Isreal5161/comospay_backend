from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Any

from app.models.notification import Notification
from app.repositories.notification_repository import NotificationRepository
from app.utils.exceptions import NotificationException, ValidationException
from app.utils.logger import log_audit_event


class InAppNotificationService:
    """Manage internal in-app notifications and persistence."""

    def __init__(
        self,
        *,
        repository: NotificationRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.logger = logger or logging.getLogger(__name__)

    async def create_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        message: str,
        notification_type: str = "info",
        category: str = "general",
        reference: str | None = None,
        metadata: dict[str, Any] | None = None,
        channel: str = "in_app",
        priority: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a generic in-app notification record."""
        self._validate_message(message)
        payload = self._build_payload(
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            category=category,
            reference=reference,
            metadata=metadata,
            channel=channel,
            priority=priority,
            expires_at=expires_at,
        )

        try:
            notification = await self.repository.create_notification(payload)
        except Exception as exc:
            self.logger.error(
                "in_app_notification_create_failed",
                extra={"user_id": str(user_id), "category": category, "error": str(exc)},
            )
            raise NotificationException(detail=str(exc)) from exc

        response = self._build_response(notification)
        self.logger.info(
            "in_app_notification_created",
            extra={
                "notification_id": str(notification.id),
                "user_id": str(user_id),
                "category": category,
                "notification_type": notification_type,
            },
        )
        log_audit_event(
            self.logger,
            "in_app_notification_created",
            notification_id=str(notification.id),
            user_id=str(user_id),
            category=category,
            notification_type=notification_type,
            priority=priority,
        )
        return response

    async def create_system_announcement(
        self,
        *,
        user_id: UUID,
        title: str,
        message: str,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a system announcement notification."""
        return await self.create_notification(
            user_id=user_id,
            title=title,
            message=message,
            notification_type="announcement",
            category="system",
            priority=priority,
            metadata=metadata,
            expires_at=expires_at,
        )

    async def create_wallet_transaction_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        transaction_type: str,
        balance: str | None = None,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a wallet transaction notification."""
        payload_data = {
            "amount": amount,
            "currency": currency,
            "transaction_type": transaction_type,
            "balance": balance,
            "reference": reference,
        }
        fallback_message = f"{transaction_type.capitalize()} of {currency} {amount} was processed."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Wallet Transaction",
            message=message,
            notification_type="wallet_transaction",
            category="wallet",
            reference=reference,
            priority=priority,
            metadata={**(metadata or {}), **payload_data},
            expires_at=expires_at,
        )

    async def create_transfer_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        source_account: str,
        destination_account: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a transfer notification."""
        fallback_message = f"Transfer of {currency} {amount} completed."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Transfer Completed",
            message=message,
            notification_type="transfer",
            category="transfer",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "source_account": source_account,
                "destination_account": destination_account,
            },
            expires_at=expires_at,
        )

    async def create_airtime_purchase_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        destination: str,
        provider_name: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create an airtime purchase notification."""
        fallback_message = f"Airtime purchase of {currency} {amount} to {destination} completed."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Airtime Purchase Successful",
            message=message,
            notification_type="airtime_purchase",
            category="airtime",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "destination": destination,
                "provider_name": provider_name,
            },
            expires_at=expires_at,
        )

    async def create_data_purchase_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        destination: str,
        data_bundle: str,
        provider_name: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a data purchase notification."""
        fallback_message = f"Data purchase of {currency} {amount} for {data_bundle} completed."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Data Purchase Complete",
            message=message,
            notification_type="data_purchase",
            category="data",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "data_bundle": data_bundle,
                "destination": destination,
                "provider_name": provider_name,
            },
            expires_at=expires_at,
        )

    async def create_electricity_purchase_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        meter_number: str,
        distributor: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create an electricity purchase notification."""
        fallback_message = f"Electricity purchase of {currency} {amount} was successful."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Electricity Purchase Successful",
            message=message,
            notification_type="electricity_purchase",
            category="electricity",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "meter_number": meter_number,
                "distributor": distributor,
            },
            expires_at=expires_at,
        )

    async def create_tv_subscription_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        provider_name: str,
        subscription_period: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a TV subscription notification."""
        fallback_message = f"TV subscription for {subscription_period} has been activated."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "TV Subscription Activated",
            message=message,
            notification_type="tv_subscription",
            category="tv",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "provider_name": provider_name,
                "subscription_period": subscription_period,
            },
            expires_at=expires_at,
        )

    async def create_education_purchase_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        institution_name: str,
        course_name: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create an education purchase notification."""
        fallback_message = f"Education purchase for {course_name} at {institution_name} is complete."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Education Purchase Confirmed",
            message=message,
            notification_type="education_purchase",
            category="education",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "institution_name": institution_name,
                "course_name": course_name,
            },
            expires_at=expires_at,
        )

    async def create_giftcard_transaction_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        amount: str,
        currency: str,
        giftcard_name: str,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a gift card transaction notification."""
        fallback_message = f"Gift card transaction for {giftcard_name} was successful."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Gift Card Transaction Completed",
            message=message,
            notification_type="giftcard_transaction",
            category="giftcard",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "amount": amount,
                "currency": currency,
                "giftcard_name": giftcard_name,
            },
            expires_at=expires_at,
        )

    async def create_kyc_status_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        status: str,
        kyc_type: str | None = None,
        reason: str | None = None,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a KYC status notification."""
        fallback_message = f"Your KYC status is now {status}."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "KYC Status Updated",
            message=message,
            notification_type="kyc_status",
            category="kyc",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "status": status,
                "kyc_type": kyc_type,
                "reason": reason,
            },
            expires_at=expires_at,
        )

    async def create_login_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        ip_address: str | None = None,
        location: str | None = None,
        device_name: str | None = None,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a login notification."""
        fallback_message = f"A new login was detected from {device_name or 'an unknown device'}."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "New Login Detected",
            message=message,
            notification_type="login_alert",
            category="security",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "ip_address": ip_address,
                "location": location,
                "device_name": device_name,
            },
            expires_at=expires_at,
        )

    async def create_security_notification(
        self,
        *,
        user_id: UUID,
        title: str | None,
        issue: str,
        details: str | None = None,
        reference: str | None = None,
        priority: str | None = None,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a security notification."""
        fallback_message = f"{issue} requires your attention."
        custom_message = metadata.get("message") if metadata and isinstance(metadata, dict) and metadata.get("message") else None
        message = custom_message if isinstance(custom_message, str) and custom_message.strip() else fallback_message
        return await self.create_notification(
            user_id=user_id,
            title=title or "Security Alert",
            message=message,
            notification_type="security_alert",
            category="security",
            reference=reference,
            priority=priority,
            metadata={
                **(metadata or {}),
                "issue": issue,
                "details": details,
            },
            expires_at=expires_at,
        )

    async def get_user_notifications(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> dict[str, Any]:
        """Retrieve paginated notifications for a user."""
        notifications, total = await self.repository.get_user_notifications(
            user_id=user_id,
            page=page,
            page_size=page_size,
            order_by=order_by,
            descending=descending,
        )
        return {
            "items": [self._build_response(notification) for notification in notifications],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_unread_notifications(self, *, user_id: UUID) -> list[dict[str, Any]]:
        """Retrieve unread notifications for a user."""
        notifications = await self.repository.get_unread_notifications(user_id=user_id)
        return [self._build_response(notification) for notification in notifications]

    async def mark_as_read(self, *, notification_id: UUID) -> dict[str, Any]:
        """Mark a single notification as read."""
        notification = await self.repository.get_by_id(notification_id)
        if notification is None:
            raise NotificationException(detail="Notification not found.")

        updated = await self.repository.mark_as_read(notification)
        response = self._build_response(updated)
        self.logger.info("in_app_notification_marked_read", extra={"notification_id": str(notification_id), "user_id": str(updated.user_id)})
        log_audit_event(
            self.logger,
            "in_app_notification_read",
            notification_id=str(notification_id),
            user_id=str(updated.user_id),
        )
        return response

    async def mark_all_as_read(self, *, user_id: UUID) -> dict[str, Any]:
        """Mark all unread notifications for a user as read."""
        count = await self.repository.mark_all_as_read(user_id=user_id)
        self.logger.info("in_app_notifications_marked_read", extra={"user_id": str(user_id), "count": count})
        log_audit_event(
            self.logger,
            "in_app_notifications_read",
            user_id=str(user_id),
            count=count,
        )
        return {"user_id": str(user_id), "updated": count}

    def _build_payload(
        self,
        *,
        user_id: UUID,
        title: str | None,
        message: str,
        notification_type: str,
        category: str,
        reference: str | None,
        metadata: dict[str, Any] | None,
        channel: str,
        priority: str | None,
        expires_at: datetime | None,
    ) -> Notification:
        metadata_payload = self._serialize_metadata(metadata, priority=priority)
        return Notification(
            id=uuid4(),
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            category=category,
            channel=channel,
            status="pending",
            is_read=False,
            is_archived=False,
            reference=reference,
            metadata_payload=metadata_payload,
            sent_at=datetime.now(timezone.utc),
            delivered_at=None,
            read_at=None,
        )

    def _serialize_metadata(self, metadata: dict[str, Any] | None, *, priority: str | None) -> str | None:
        combined = {**(metadata or {})}
        if priority is not None:
            combined["priority"] = priority
        if not combined:
            return None
        try:
            return json.dumps(combined, default=str)
        except Exception:
            raise ValidationException("Notification metadata must be JSON serializable.")

    def _validate_message(self, message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValidationException("Notification message is required.")

    def _build_response(self, notification: Notification) -> dict[str, Any]:
        return {
            "id": str(notification.id),
            "user_id": str(notification.user_id),
            "title": notification.title,
            "message": notification.message,
            "notification_type": notification.notification_type,
            "category": notification.category,
            "channel": notification.channel,
            "status": notification.status,
            "is_read": notification.is_read,
            "is_archived": notification.is_archived,
            "reference": notification.reference,
            "metadata_payload": notification.metadata_payload,
            "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
            "delivered_at": notification.delivered_at.isoformat() if notification.delivered_at else None,
            "read_at": notification.read_at.isoformat() if notification.read_at else None,
            "created_at": notification.created_at.isoformat(),
            "updated_at": notification.updated_at.isoformat(),
        }


__all__ = ["InAppNotificationService"]
