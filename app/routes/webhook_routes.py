from __future__ import annotations

"""Webhook route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.webhook_controller import WebhookController
from app.repositories.notification_repository import NotificationRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.routes.airtime_routes import get_airtime_service as get_airtime_domain_service
from app.routes.data_routes import get_data_service as get_data_domain_service
from app.routes.education_routes import get_education_service as get_education_domain_service
from app.routes.electricity_routes import get_electricity_service as get_electricity_domain_service
from app.routes.notification_routes import get_notification_service as get_notification_domain_service
from app.routes.payment_routes import get_payment_service as get_payment_domain_service
from app.routes.tv_routes import get_tv_service as get_tv_domain_service
from app.routes.wallet_routes import get_wallet_service as get_wallet_domain_service
from app.services.giftcard.pricing import GiftCardPricingService
from app.services.giftcard.reconciliation import GiftCardReconciliationService
from app.services.giftcard.settlement import GiftCardSettlementService
from app.services.giftcard.trading import GiftCardTradingService
from app.services.giftcard.valuation import GiftCardValuationService
from app.services.giftcard_service import GiftCardService
from app.services.notification.email import EmailNotificationService
from app.services.notification.in_app import InAppNotificationService
from app.services.notification.preferences import NotificationPreferencesService
from app.services.notification.push import PushNotificationService
from app.services.notification.sms import SMSNotificationService
from app.services.notification_service import NotificationService, build_notification_service
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService
from app.services.webhook.cloud import CloudWebhookService
from app.services.webhook.education import EducationWebhookService
from app.services.webhook.flutterwave import FlutterwaveWebhookService
from app.services.webhook.giftcard import GiftCardWebhookService
from app.services.webhook.notification import NotificationWebhookService
from app.services.webhook.security import WebhookSecurityService
from app.services.webhook.vtu import VTUWebhookService
from app.services.webhook_service import WebhookService


router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


async def get_webhook_service(session: AsyncSession = Depends(get_db)) -> WebhookService:
    """Compose the webhook service graph per request using the active database session."""
    payment_service = await get_payment_domain_service(session=session)
    wallet_service = await get_wallet_domain_service(session=session)
    airtime_service = await get_airtime_domain_service(session=session)
    data_service = await get_data_domain_service(session=session)
    electricity_service = await get_electricity_domain_service(session=session)
    tv_service = await get_tv_domain_service(session=session)
    education_service = await get_education_domain_service(session=session)
    notification_service = _build_notification_service(session=session)
    provider_service = _build_provider_service(session=session)

    flutterwave_service = FlutterwaveWebhookService(
        payment_service=payment_service,
        wallet_service=wallet_service,
        provider_service=provider_service,
    )
    vtu_service = VTUWebhookService(
        airtime_service=airtime_service,
        data_service=data_service,
        electricity_service=electricity_service,
        tv_service=tv_service,
        provider_service=provider_service,
    )
    education_webhook_service = EducationWebhookService(education_service=education_service)
    giftcard_service = _build_giftcard_service(session=session, provider_service=provider_service)
    giftcard_webhook_service = GiftCardWebhookService(giftcard_service=giftcard_service)
    notification_webhook_service = NotificationWebhookService(notification_service=notification_service)
    cloud_service = CloudWebhookService(provider_service=provider_service)
    security_service = WebhookSecurityService()

    return WebhookService(
        flutterwave_service=flutterwave_service,
        vtu_service=vtu_service,
        education_service=education_webhook_service,
        giftcard_service=giftcard_webhook_service,
        notification_service=notification_webhook_service,
        cloud_service=cloud_service,
        security_service=security_service,
    )


async def get_webhook_controller(
    webhook_service: WebhookService = Depends(get_webhook_service),
) -> WebhookController:
    """Instantiate the webhook controller with a request-scoped webhook service."""
    return WebhookController(webhook_service)


def _build_provider_service(*, session: AsyncSession) -> ProviderService:
    provider_repository = ProviderRepository(session=session)
    provider_selector = ProviderSelector(provider_repository=provider_repository)
    provider_health_service = ProviderHealthService(provider_repository=provider_repository)
    provider_failover_service = ProviderFailoverService(
        selector=provider_selector,
        health_service=provider_health_service,
    )
    return ProviderService(
        selector=provider_selector,
        health_service=provider_health_service,
        failover_service=provider_failover_service,
        provider_repository=provider_repository,
    )


def _build_giftcard_service(*, session: AsyncSession, provider_service: ProviderService) -> GiftCardService:
    from app.repositories.transaction_repository import TransactionRepository
    from app.repositories.user_repository import UserRepository
    from app.repositories.wallet_repository import WalletRepository

    transaction_repository = TransactionRepository(session=session)
    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)

    wallet_service = _build_wallet_service(session=session, user_repository=user_repository, wallet_repository=wallet_repository, transaction_repository=transaction_repository)
    trading_service = GiftCardTradingService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
    )
    settings_repository = SystemSettingsRepository(session=session)
    valuation_service = GiftCardValuationService(
        provider_service=provider_service,
        settings_repository=settings_repository,
    )
    pricing_service = GiftCardPricingService(
        provider_service=provider_service,
        settings_repository=settings_repository,
    )
    settlement_service = GiftCardSettlementService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
    )
    reconciliation_service = GiftCardReconciliationService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
    )

    return GiftCardService(
        trading_service=trading_service,
        valuation_service=valuation_service,
        pricing_service=pricing_service,
        settlement_service=settlement_service,
        reconciliation_service=reconciliation_service,
    )


def _build_wallet_service(
    *,
    session: AsyncSession,
    user_repository,
    wallet_repository,
    transaction_repository,
):
    from app.services.wallet import (
        WalletFundingService,
        WalletManager,
        WalletPinService,
        WalletStatementService,
        WalletTransferService,
    )
    from app.services.wallet_service import WalletService
    from app.repositories.virtual_account_repository import VirtualAccountRepository
    from app.routes.notification_routes import build_notification_service
    from app.services.virtual_account_service import VirtualAccountService
    from app.integrations.payments.flutterwave.client import FlutterwaveClient
    from app.integrations.payments.flutterwave.virtual_accounts import FlutterwaveVirtualAccountService

    wallet_manager = WalletManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        virtual_account_service=VirtualAccountService(
            virtual_account_repository=VirtualAccountRepository(session=session),
            wallet_repository=wallet_repository,
            user_repository=user_repository,
            provider_services={"flutterwave": FlutterwaveVirtualAccountService(client=FlutterwaveClient())},
            notification_service=build_notification_service(session=session, redis_client=None),
            session=session,
        ),
        session=session,
    )
    funding_service = WalletFundingService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        session=session,
    )
    transfer_service = WalletTransferService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        session=session,
    )
    pin_service = WalletPinService(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        session=session,
    )
    statement_service = WalletStatementService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        session=session,
    )

    return WalletService(
        wallet_manager=wallet_manager,
        funding_service=funding_service,
        transfer_service=transfer_service,
        pin_service=pin_service,
        statement_service=statement_service,
    )


def _build_notification_service(*, session: AsyncSession) -> NotificationService:
    return build_notification_service(session=session, redis_client=None)


@router.post("/payment", status_code=status.HTTP_200_OK)
async def handle_payment_webhook(
    request: Request,
    controller: WebhookController = Depends(get_webhook_controller),
) -> dict[str, Any]:
    return await controller.handle_payment_webhook(request)


@router.post("/virtual-account", status_code=status.HTTP_200_OK)
async def handle_virtual_account_webhook(
    request: Request,
    controller: WebhookController = Depends(get_webhook_controller),
) -> dict[str, Any]:
    return await controller.handle_virtual_account_webhook(request)


@router.post("/transfer", status_code=status.HTTP_200_OK)
async def handle_transfer_webhook(
    request: Request,
    controller: WebhookController = Depends(get_webhook_controller),
) -> dict[str, Any]:
    return await controller.handle_transfer_webhook(request)


@router.post("/provider", status_code=status.HTTP_200_OK)
async def handle_provider_webhook(
    request: Request,
    controller: WebhookController = Depends(get_webhook_controller),
) -> dict[str, Any]:
    return await controller.handle_provider_webhook(request)


@router.post("/notification", status_code=status.HTTP_200_OK)
async def handle_notification_webhook(
    request: Request,
    controller: WebhookController = Depends(get_webhook_controller),
) -> dict[str, Any]:
    return await controller.handle_notification_webhook(request)
