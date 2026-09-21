from __future__ import annotations

import logging
from typing import Any

from app.services.airtime_service import AirtimeService
from app.services.data_service import DataService
from app.services.education_service import EducationService
from app.services.electricity_service import ElectricityService
from app.services.giftcard_service import GiftCardService
from app.services.notification_service import NotificationService
from app.services.payment_service import PaymentService
from app.services.provider_service import ProviderService
from app.services.tv_service import TVService
from app.services.wallet_service import WalletService
from app.services.webhook.cloud import CloudWebhookService
from app.services.webhook.dispatcher import WebhookDispatcher
from app.services.webhook.education import EducationWebhookService
from app.services.webhook.events import WebhookEventService
from app.services.webhook.flutterwave import FlutterwaveWebhookService
from app.services.webhook.giftcard import GiftCardWebhookService
from app.services.webhook.idempotency import IdempotencyService
from app.services.webhook.notification import NotificationWebhookService
from app.services.webhook.security import WebhookSecurityService
from app.services.webhook.vtu import VTUWebhookService
from app.utils.exceptions import ValidationException


class WebhookService:
    """Thin facade for webhook processing, delegating to internal webhook modules."""

    def __init__(
        self,
        *,
        flutterwave_service: FlutterwaveWebhookService | None = None,
        vtu_service: VTUWebhookService | None = None,
        education_service: EducationWebhookService | None = None,
        giftcard_service: GiftCardWebhookService | None = None,
        notification_service: NotificationWebhookService | None = None,
        cloud_service: CloudWebhookService | None = None,
        security_service: WebhookSecurityService | None = None,
        idempotency_service: IdempotencyService | None = None,
        dispatcher: WebhookDispatcher | None = None,
        event_service: WebhookEventService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.flutterwave_service = flutterwave_service
        self.vtu_service = vtu_service
        self.education_service = education_service
        self.giftcard_service = giftcard_service
        self.notification_service = notification_service
        self.cloud_service = cloud_service
        self.security_service = security_service or WebhookSecurityService(logger=self.logger)
        self.idempotency_service = idempotency_service or IdempotencyService(logger=self.logger)
        self.dispatcher = dispatcher or WebhookDispatcher(logger=self.logger)
        self.event_service = event_service or WebhookEventService(logger=self.logger)

    async def process_flutterwave_webhook(self, *, provider_name: str, event_id: str | None, payload: dict[str, Any], signature: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        if self.flutterwave_service is None:
            raise ValidationException("Flutterwave webhook processing dependency is required.")
        return await self.flutterwave_service.process_payment_webhook(provider_name=provider_name, event_id=event_id, payload=payload, signature=signature, timestamp=timestamp, secret=secret)

    async def process_vtu_webhook(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.vtu_service is None:
            raise ValidationException("VTU webhook processing dependency is required.")
        if provider_name.lower() in {"aida", "aidapay", "clubkonnect", "vtugate", "vtu.ng", "vtu-ng"}:
            return await self.vtu_service.process_airtime_callback(provider_name=provider_name, payload=payload)
        raise ValidationException("Unsupported VTU webhook provider.")

    async def process_education_webhook(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.education_service is None:
            raise ValidationException("Education webhook processing dependency is required.")
        return await self.education_service.process_purchase_update(payload=payload)

    async def process_giftcard_webhook(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        if self.giftcard_service is None:
            raise ValidationException("Gift-card webhook processing dependency is required.")
        return await self.giftcard_service.process_status_update(payload=payload)

    async def process_notification_webhook(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        if self.notification_service is None:
            raise ValidationException("Notification webhook processing dependency is required.")
        return await self.notification_service.process_status_update(payload=payload)

    async def process_cloud_webhook(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        if self.cloud_service is None:
            raise ValidationException("Cloud webhook processing dependency is required.")
        return await self.cloud_service.process_upload_callback(payload=payload)

    async def verify_signature(self, *, payload: bytes, signature: str | None, timestamp: str | None, secret: str | None) -> bool:
        return await self.security_service.verify_signature(payload=payload, signature=signature, timestamp=timestamp, secret=secret)

    async def validate_request(self, *, payload: dict[str, Any], provider_name: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        return await self.security_service.validate_request(payload=payload, provider_name=provider_name, timestamp=timestamp, secret=secret)

    async def check_idempotency(self, *, event_id: str | None, provider_name: str | None) -> bool:
        return await self.idempotency_service.check_idempotency(event_id=event_id, provider_name=provider_name)

    async def dispatch_event(self, *, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.event_service.dispatch_event(event_type=event_type, payload=payload)

    async def publish_event(self, *, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.event_service.publish_event(event_type=event_type, payload=payload)
