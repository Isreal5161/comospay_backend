from __future__ import annotations

"""Airtime route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.airtime_controller import (
    AirtimeController,
    AirtimeDetailRequest,
    AirtimeHistoryRequest,
    AirtimePricingRequest,
    AirtimePurchaseRequest,
    AirtimeReconciliationRequest,
    AirtimeStatusRequest,
    AirtimeValidationRequest,
)
from app.repositories.provider_repository import ProviderRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.airtime.purchase import AirtimePurchaseService
from app.services.airtime.reconciliation import AirtimeReconciliationService
from app.services.airtime.validation import AirtimeValidationService
from app.services.airtime_service import AirtimeService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService
from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
)
from app.services.wallet_service import WalletService


router = APIRouter(prefix="/airtime", tags=["Airtime"])


async def get_airtime_service(session: AsyncSession = Depends(get_db)) -> AirtimeService:
    """Compose the airtime service graph per request using the active database session."""
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

    wallet_service = _build_wallet_service(
        session=session,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
    )
    purchase_service = AirtimePurchaseService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
    )
    validation_service = AirtimeValidationService(wallet_service=wallet_service)
    reconciliation_service = AirtimeReconciliationService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        wallet_repository=wallet_repository,
    )

    return AirtimeService(
        purchase_service=purchase_service,
        validation_service=validation_service,
        pricing_service=None,
        reconciliation_service=reconciliation_service,
    )


def _build_wallet_service(
    *,
    session: AsyncSession,
    user_repository: UserRepository,
    wallet_repository: WalletRepository,
    transaction_repository: TransactionRepository,
) -> WalletService:
    # Compose a request-scoped VirtualAccountService and inject into WalletManager
    from app.repositories.virtual_account_repository import VirtualAccountRepository
    from app.routes.notification_routes import build_notification_service
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


async def get_airtime_controller(airtime_service: AirtimeService = Depends(get_airtime_service)) -> AirtimeController:
    """Instantiate the airtime controller with a request-scoped airtime service."""
    return AirtimeController(airtime_service)


@router.post("/purchase", status_code=status.HTTP_201_CREATED)
async def purchase_airtime(
    payload: AirtimePurchaseRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.purchase_airtime(payload)


@router.post("/validate", status_code=status.HTTP_200_OK)
async def validate_purchase_request(
    payload: AirtimeValidationRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.validate_purchase_request(payload)


@router.post("/price", status_code=status.HTTP_200_OK)
async def get_price(
    payload: AirtimePricingRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.get_price(payload)


@router.post("/pricing", status_code=status.HTTP_200_OK)
async def get_pricing_breakdown(
    payload: AirtimePricingRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.get_pricing_breakdown(payload)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_status(
    reference: str,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_status(reference)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_transaction(
    payload: AirtimeReconciliationRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.reconcile_transaction(payload)


@router.post("/history", status_code=status.HTTP_200_OK)
async def get_purchase_history(
    payload: AirtimeHistoryRequest,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_history(payload)


@router.get("/details/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_details(
    reference: str,
    controller: AirtimeController = Depends(get_airtime_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_details(reference)
