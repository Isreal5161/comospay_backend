from __future__ import annotations

"""TV subscription route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.tv_controller import (
    TVController,
    TVDetailRequest,
    TVHistoryRequest,
    TVPackageRequest,
    TVPricingRequest,
    TVProviderRequest,
    TVReconciliationRequest,
    TVRenewalRequest,
    TVStatusRequest,
    TVSubscriptionRequest,
    TVValidationRequest,
)
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.routes.notification_routes import build_notification_service
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService
from app.services.tv.packages import TVPackageService
from app.services.tv.pricing import TVPricingService
from app.services.tv.purchase import TVPurchaseService
from app.services.tv.reconciliation import TVReconciliationService
from app.services.tv.validation import TVValidationService
from app.services.tv_service import TVService
from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
)
from app.services.wallet_service import WalletService


router = APIRouter(prefix="/tv", tags=["TV"])


async def get_tv_service(session: AsyncSession = Depends(get_db)) -> TVService:
    """Compose the TV service graph per request using the active database session."""
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

    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)
    transaction_repository = TransactionRepository(session=session)
    settings_repository = SystemSettingsRepository(session=session)

    wallet_service = _build_wallet_service(
        session=session,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
    )
    validation_service = TVValidationService(
        wallet_service=wallet_service,
        provider_repository=provider_repository,
    )
    package_service = TVPackageService(provider_service=provider_service)
    pricing_service = TVPricingService(
        provider_service=provider_service,
        settings_repository=settings_repository,
    )
    purchase_service = TVPurchaseService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
    )
    reconciliation_service = TVReconciliationService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        settings_repository=settings_repository,
    )

    return TVService(
        purchase_service=purchase_service,
        validation_service=validation_service,
        package_service=package_service,
        pricing_service=pricing_service,
        reconciliation_service=reconciliation_service,
    )


def _build_wallet_service(
    *,
    session: AsyncSession,
    user_repository: UserRepository,
    wallet_repository: WalletRepository,
    transaction_repository: TransactionRepository,
) -> WalletService:
    from app.repositories.virtual_account_repository import VirtualAccountRepository
    from app.services.virtual_account_service import VirtualAccountService
    from app.integrations.payments.flutterwave.client import FlutterwaveClient
    from app.integrations.payments.flutterwave.virtual_accounts import FlutterwaveVirtualAccountService

    virtual_account_repository = VirtualAccountRepository(session=session)
    flutterwave_client = FlutterwaveClient()
    flutterwave_va = FlutterwaveVirtualAccountService(client=flutterwave_client)
    provider_map = {"flutterwave": flutterwave_va}

    virtual_account_service = VirtualAccountService(
        virtual_account_repository=virtual_account_repository,
        wallet_repository=wallet_repository,
        user_repository=user_repository,
        provider_services=provider_map,
        notification_service=build_notification_service(session=session, redis_client=None),
        session=session,
    )

    wallet_manager = WalletManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        virtual_account_service=virtual_account_service,
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


async def get_tv_controller(tv_service: TVService = Depends(get_tv_service)) -> TVController:
    """Instantiate the TV controller with a request-scoped TV service."""
    return TVController(tv_service)


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
async def subscribe_tv(
    request: Request,
    payload: TVSubscriptionRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.purchase_tv(payload, request=request)


@router.post("/validate", status_code=status.HTTP_200_OK)
async def validate_smart_card(
    payload: TVValidationRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.validate_smart_card_number(payload)


@router.get("/packages", status_code=status.HTTP_200_OK)
async def get_packages(
    payload: TVPackageRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_packages(payload)


@router.post("/price", status_code=status.HTTP_200_OK)
async def get_price(
    payload: TVPricingRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_price(payload)


@router.post("/renew", status_code=status.HTTP_200_OK)
async def renew_subscription(
    payload: TVRenewalRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.renew_subscription(payload)


@router.get("/providers", status_code=status.HTTP_200_OK)
async def get_providers(
    payload: TVProviderRequest,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_providers(payload)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_status(
    reference: str,
    request: Request,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_status(reference, request=request)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_transaction(
    payload: TVReconciliationRequest,
    request: Request,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.reconcile_transaction(payload, request=request)


@router.post("/history", status_code=status.HTTP_200_OK)
async def get_purchase_history(
    payload: TVHistoryRequest,
    request: Request,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_history(payload, request=request)


@router.get("/details/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_details(
    reference: str,
    request: Request,
    controller: TVController = Depends(get_tv_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_details(reference, request=request)
