from __future__ import annotations

"""Education route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.education_controller import EducationController
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.education.pricing import EducationPricingService
from app.services.education.purchase import EducationPurchaseService
from app.services.education.reconciliation import EducationReconciliationService
from app.services.education.result import EducationResultService
from app.services.education.validation import EducationValidationService
from app.services.education_service import EducationService
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
from app.routes.notification_routes import build_notification_service
from app.services.wallet_service import WalletService


router = APIRouter(prefix="/education", tags=["Education"])


async def get_education_service(session: AsyncSession = Depends(get_db)) -> EducationService:
    """Compose the education service graph per request using the active database session."""
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
    validation_service = EducationValidationService(
        wallet_service=wallet_service,
        provider_repository=provider_repository,
    )
    pricing_service = EducationPricingService(
        provider_service=provider_service,
        settings_repository=settings_repository,
    )
    result_service = EducationResultService()
    purchase_service = EducationPurchaseService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
    )
    reconciliation_service = EducationReconciliationService(
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        settings_repository=settings_repository,
    )

    return EducationService(
        purchase_service=purchase_service,
        validation_service=validation_service,
        pricing_service=pricing_service,
        result_service=result_service,
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


async def get_education_controller(
    education_service: EducationService = Depends(get_education_service),
) -> EducationController:
    """Instantiate the education controller with a request-scoped education service."""
    return EducationController(education_service)


@router.post("/waec", status_code=status.HTTP_201_CREATED)
async def purchase_waec(
    request: Request,
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.purchase_waec(payload, request=request)


@router.post("/neco", status_code=status.HTTP_201_CREATED)
async def purchase_neco(
    request: Request,
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.purchase_neco(payload, request=request)


@router.post("/nabteb", status_code=status.HTTP_201_CREATED)
async def purchase_nabteb(
    request: Request,
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.purchase_nabteb(payload, request=request)


@router.post("/jamb", status_code=status.HTTP_201_CREATED)
async def purchase_jamb(
    request: Request,
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.purchase_jamb(payload, request=request)


@router.post("/remita", status_code=status.HTTP_201_CREATED)
async def purchase_remita(
    request: Request,
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.purchase_remita(payload, request=request)


@router.post("/validate", status_code=status.HTTP_200_OK)
async def validate_service(
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.validate_service(payload)


@router.get("/services", status_code=status.HTTP_200_OK)
async def get_services(
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.get_services(payload)


@router.post("/price", status_code=status.HTTP_200_OK)
async def get_price(
    payload: Any,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.get_price(payload)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_status(
    reference: str,
    request: Request,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_status(reference, request=request)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_transaction(
    payload: Any,
    request: Request,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.reconcile_transaction(payload, request=request)


@router.post("/history", status_code=status.HTTP_200_OK)
async def get_purchase_history(
    payload: Any,
    request: Request,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_history(payload, request=request)


@router.get("/details/{reference}", status_code=status.HTTP_200_OK)
async def get_purchase_details(
    reference: str,
    request: Request,
    controller: EducationController = Depends(get_education_controller),
) -> dict[str, Any]:
    return await controller.get_purchase_details(reference, request=request)
