from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.notification_preferences_repository import NotificationPreferencesRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.provider_repository import ProviderRepository
from app.services.notification.email import EmailNotificationService
from app.services.notification.in_app import InAppNotificationService
from app.services.notification.preferences import NotificationPreferencesService
from app.services.notification.push import PushNotificationService
from app.services.notification.sms import SMSNotificationService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService


def build_notification_service(*, session: AsyncSession, redis_client: Any | None = None) -> "NotificationService":
    provider_repository = ProviderRepository(session=session)
    provider_selector = ProviderSelector(provider_repository=provider_repository)
    provider_health_service = ProviderHealthService(provider_repository=provider_repository)
    provider_failover_service = ProviderFailoverService(
        selector=provider_selector,
        health_service=provider_health_service,
    )
    provider_service = ProviderService(
        selector=provider_selector,
        health_service=provider_health_service,
        failover_service=provider_failover_service,
        provider_repository=provider_repository,
    )
    notification_repository = NotificationRepository(session=session)
    preferences_repository = NotificationPreferencesRepository(session=session)
    return NotificationService(
        email_service=EmailNotificationService(provider_service=provider_service),
        sms_service=SMSNotificationService(provider_service=provider_service),
        push_service=PushNotificationService(provider_service=provider_service),
        in_app_service=InAppNotificationService(repository=notification_repository),
        preferences_service=NotificationPreferencesService(
            preferences_repository=preferences_repository,
            redis_client=redis_client,
        ),
    )


class NotificationService:
    """Public facade for notification workflows, delegating to subdomain services."""

    def __init__(
        self,
        *,
        email_service: EmailNotificationService,
        sms_service: SMSNotificationService,
        push_service: PushNotificationService,
        in_app_service: InAppNotificationService,
        preferences_service: NotificationPreferencesService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.email_service = email_service
        self.sms_service = sms_service
        self.push_service = push_service
        self.in_app_service = in_app_service
        self.preferences_service = preferences_service
        self.logger = logger or logging.getLogger(__name__)

    async def send_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate email delivery to the email notification service."""
        self._log_entry("send_email")
        return await self.email_service.send_email(*args, **kwargs)

    async def send_transactional_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transactional email delivery to the email notification service."""
        self._log_entry("send_transactional_email")
        return await self.email_service.send_transactional_email(*args, **kwargs)

    async def send_otp_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate OTP email delivery to the email notification service."""
        self._log_entry("send_otp_email")
        return await self.email_service.send_otp_email(*args, **kwargs)

    async def send_password_reset_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate password reset email delivery to the email notification service."""
        self._log_entry("send_password_reset_email")
        return await self.email_service.send_password_reset_email(*args, **kwargs)

    async def send_welcome_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate welcome email delivery to the email notification service."""
        self._log_entry("send_welcome_email")
        return await self.email_service.send_welcome_email(*args, **kwargs)

    async def send_wallet_funding_notification_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate wallet funding email delivery to the email notification service."""
        self._log_entry("send_wallet_funding_notification_email")
        return await self.email_service.send_wallet_funding_notification(*args, **kwargs)

    async def send_transfer_notification_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transfer email delivery to the email notification service."""
        self._log_entry("send_transfer_notification_email")
        return await self.email_service.send_transfer_notification(*args, **kwargs)

    async def send_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate SMS delivery to the SMS notification service."""
        self._log_entry("send_sms")
        return await self.sms_service.send_sms(*args, **kwargs)

    async def send_otp_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate OTP SMS delivery to the SMS notification service."""
        self._log_entry("send_otp_sms")
        return await self.sms_service.send_otp_sms(*args, **kwargs)

    async def send_transaction_alert_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transaction alert SMS delivery to the SMS notification service."""
        self._log_entry("send_transaction_alert_sms")
        return await self.sms_service.send_transaction_alert(*args, **kwargs)

    async def send_wallet_funding_notification_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate wallet funding SMS delivery to the SMS notification service."""
        self._log_entry("send_wallet_funding_notification_sms")
        return await self.sms_service.send_wallet_funding_notification(*args, **kwargs)

    async def send_transfer_notification_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transfer SMS delivery to the SMS notification service."""
        self._log_entry("send_transfer_notification_sms")
        return await self.sms_service.send_transfer_notification(*args, **kwargs)

    async def send_kyc_notification_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate KYC notification SMS delivery to the SMS notification service."""
        self._log_entry("send_kyc_notification_sms")
        return await self.sms_service.send_kyc_notification(*args, **kwargs)

    async def send_security_alert_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate security alert SMS delivery to the SMS notification service."""
        self._log_entry("send_security_alert_sms")
        return await self.sms_service.send_security_alert(*args, **kwargs)

    async def send_push(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate push notification delivery to the push notification service."""
        self._log_entry("send_push")
        return await self.push_service.send_push(*args, **kwargs)

    async def send_transaction_notification_push(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transaction push notification delivery to the push notification service."""
        self._log_entry("send_transaction_notification_push")
        return await self.push_service.send_transaction_notification(*args, **kwargs)

    async def send_wallet_funding_notification_push(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate wallet funding push notification delivery to the push notification service."""
        self._log_entry("send_wallet_funding_notification_push")
        return await self.push_service.send_wallet_funding_notification(*args, **kwargs)

    async def send_wallet_debit_notification_push(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate wallet debit push notification delivery to the push notification service."""
        self._log_entry("send_wallet_debit_notification_push")
        return await self.push_service.send_wallet_debit_notification(*args, **kwargs)

    async def create_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate in-app notification creation to the in-app notification service."""
        self._log_entry("create_notification")
        return await self.in_app_service.create_notification(*args, **kwargs)

    async def create_system_announcement(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate system announcement creation to the in-app notification service."""
        self._log_entry("create_system_announcement")
        return await self.in_app_service.create_system_announcement(*args, **kwargs)

    async def create_wallet_transaction_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate wallet transaction notification creation to the in-app notification service."""
        self._log_entry("create_wallet_transaction_notification")
        return await self.in_app_service.create_wallet_transaction_notification(*args, **kwargs)

    async def create_transfer_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate transfer notification creation to the in-app notification service."""
        self._log_entry("create_transfer_notification")
        return await self.in_app_service.create_transfer_notification(*args, **kwargs)

    async def create_airtime_purchase_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate airtime purchase notification creation to the in-app notification service."""
        self._log_entry("create_airtime_purchase_notification")
        return await self.in_app_service.create_airtime_purchase_notification(*args, **kwargs)

    async def create_data_purchase_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate data purchase notification creation to the in-app notification service."""
        self._log_entry("create_data_purchase_notification")
        return await self.in_app_service.create_data_purchase_notification(*args, **kwargs)

    async def create_electricity_purchase_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate electricity purchase notification creation to the in-app notification service."""
        self._log_entry("create_electricity_purchase_notification")
        return await self.in_app_service.create_electricity_purchase_notification(*args, **kwargs)

    async def get_user_notifications(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Retrieve paginated user notifications via the in-app notification service."""
        self._log_entry("get_user_notifications")
        return await self.in_app_service.get_user_notifications(*args, **kwargs)

    async def get_unread_notifications(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Retrieve unread notifications for a user via the in-app notification service."""
        self._log_entry("get_unread_notifications")
        return await self.in_app_service.get_unread_notifications(*args, **kwargs)

    async def mark_as_read(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Mark a single notification as read via the in-app notification service."""
        self._log_entry("mark_as_read")
        return await self.in_app_service.mark_as_read(*args, **kwargs)

    async def mark_all_as_read(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Mark all notifications as read for a user via the in-app notification service."""
        self._log_entry("mark_all_as_read")
        return await self.in_app_service.mark_all_as_read(*args, **kwargs)

    async def delete_notification(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delete a notification via the in-app notification repository (delegated)."""
        self._log_entry("delete_notification")
        # Delegate to repository via in_app_service; in_app service exposes limited API,
        # but repository delete is straightforward and kept within the service layer.
        # Prefer exposing a method on in_app_service; if missing, access repository here
        # as a controlled delegation.
        repo = getattr(self.in_app_service, "repository", None)
        if repo is None or not hasattr(repo, "delete_notification"):
            # Defensive: raise a NotificationException to keep behavior consistent.
            from app.utils.exceptions import NotificationException

            raise NotificationException(detail="Notification deletion is not available.")

        result = await repo.delete_notification(kwargs.get("notification_id") or (args[0] if args else None))
        return result

    async def get_preferences(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate preference retrieval to the notification preferences service."""
        self._log_entry("get_preferences")
        return await self.preferences_service.get_preferences(*args, **kwargs)

    async def create_default_preferences(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate default preference creation to the notification preferences service."""
        self._log_entry("create_default_preferences")
        return await self.preferences_service.create_default_preferences(*args, **kwargs)

    async def update_preferences(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate preference updates to the notification preferences service."""
        self._log_entry("update_preferences")
        return await self.preferences_service.update_preferences(*args, **kwargs)

    async def enable_channel(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate channel toggling to the notification preferences service."""
        self._log_entry("enable_channel")
        return await self.preferences_service.enable_channel(*args, **kwargs)

    async def set_global_notifications(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate global notification toggling to the notification preferences service."""
        self._log_entry("set_global_notifications")
        return await self.preferences_service.set_global_notifications(*args, **kwargs)

    async def set_notification_frequency(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate frequency updates to the notification preferences service."""
        self._log_entry("set_notification_frequency")
        return await self.preferences_service.set_notification_frequency(*args, **kwargs)

    async def set_quiet_hours(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate quiet hours updates to the notification preferences service."""
        self._log_entry("set_quiet_hours")
        return await self.preferences_service.set_quiet_hours(*args, **kwargs)

    async def set_category_preference(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate category preference updates to the notification preferences service."""
        self._log_entry("set_category_preference")
        return await self.preferences_service.set_category_preference(*args, **kwargs)

    async def enable_email(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate email channel enabling to the notification preferences service."""
        self._log_entry("enable_email")
        return await self.preferences_service.enable_email(*args, **kwargs)

    async def enable_sms(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate SMS channel enabling to the notification preferences service."""
        self._log_entry("enable_sms")
        return await self.preferences_service.enable_sms(*args, **kwargs)

    async def enable_push(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate push channel enabling to the notification preferences service."""
        self._log_entry("enable_push")
        return await self.preferences_service.enable_push(*args, **kwargs)

    async def enable_in_app(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate in-app channel enabling to the notification preferences service."""
        self._log_entry("enable_in_app")
        return await self.preferences_service.enable_in_app(*args, **kwargs)

    def _log_entry(self, action: str, **metadata: Any) -> None:
        self.logger.info("notification_service_delegation", extra={"action": action, **metadata})
