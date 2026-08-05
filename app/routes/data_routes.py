from __future__ import annotations

"""Data route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.data_controller import (
    DataController,
    DataDetailRequest,
    DataHistoryRequest,
    DataPlanValidationRequest,
    DataPlansRequest,
    DataPricingRequest,
    DataPurchaseRequest,
    DataReconciliationRequest,
    DataStatusRequest,
)
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.data.plans import DataPlanService
from app.services.data.pricing import DataPricingService
from app.services.data.purchase import DataPurchaseService
from app.services.data.reconciliation import DataReconciliationService
from app.services.data.validation import DataValidationService
from app.services.data_service import DataService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService
from app.routes.notification_routes import build_notification_service
from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
)
from app.services.wallet_service import WalletService


router = APIRouter(prefix="/data", tags=["Data"])


async def get_data_service(session: AsyncSession = Depends(get_db)) -> DataService:
    """Compose the data service graph per request using the active database session."""
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
    purchase_service = DataPurchaseService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
    )
    validation_service = DataValidationService(wallet_service=wallet_service)
    pricing_service = DataPricingService(
        provider_service=provider_service,
        settings_repository=settings_repository,
    )
    plan_service = DataPlanService(
        provider_service=provider_service,
    )
    reconciliation_service = DataReconciliationService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        wallet_repository=wallet_repository,
    )

    return DataService(
        purchase_service=purchase_service,
        plan_service=plan_service,
        validation_service=validation_service,
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


async def get_data_controller(data_service: DataService = Depends(get_data_service)) -> DataController:
    """Instantiate the data controller with a request-scoped data service."""
    return DataController(data_service)


@router.post("/purchase", status_code=status.HTTP_201_CREATED)
async def purchase_data(
    payload: DataPurchaseRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.purchase_data(payload)


@router.get("/plans", status_code=status.HTTP_200_OK)
async def get_data_plans(
    payload: DataPlansRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.get_data_plans(payload)


@router.post("/plans/validate", status_code=status.HTTP_200_OK)
async def validate_plan(
    payload: DataPlanValidationRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.validate_plan(payload)


@router.post("/price", status_code=status.HTTP_200_OK)
async def get_price(
    payload: DataPricingRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.get_price(payload)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_status(
    reference: str,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_status(reference)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_transaction(
    payload: DataReconciliationRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.reconcile_transaction(payload)


@router.post("/history", status_code=status.HTTP_200_OK)
async def get_purchase_history(
    payload: DataHistoryRequest,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_history(payload)


@router.get("/details/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_details(
    reference: str,
    controller: DataController = Depends(get_data_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_details(reference)
