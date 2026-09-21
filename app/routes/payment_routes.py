from __future__ import annotations

"""Payment route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.payment_controller import (
    PaymentCancellationRequest,
    PaymentCollectionRequest,
    PaymentController,
    PaymentDetailRequest,
    PaymentHistoryRequest,
    PaymentInitializeRequest,
    PaymentReconciliationRequest,
    PaymentVerificationRequest,
)
from app.repositories.provider_repository import ProviderRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.payment.payment import PaymentManager
from app.services.payment.webhook import PaymentWebhookService
from app.services.payment_service import PaymentService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService


router = APIRouter(prefix="/payments", tags=["Payments"])


async def get_payment_service(session: AsyncSession = Depends(get_db)) -> PaymentService:
    """Compose the payment service graph per request using the active database session."""
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

    payment_manager = PaymentManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=provider_service,
    )
    webhook_service = PaymentWebhookService(
        transaction_repository=transaction_repository,
        wallet_repository=wallet_repository,
        provider_service=provider_service,
    )

    return PaymentService(payment_manager=payment_manager, webhook_service=webhook_service)


async def get_payment_controller(payment_service: PaymentService = Depends(get_payment_service)) -> PaymentController:
    """Instantiate the payment controller with a request-scoped payment service."""
    return PaymentController(payment_service)


@router.post("", status_code=status.HTTP_201_CREATED)
async def initialize_payment(
    request: Request,
    payload: PaymentInitializeRequest,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.initialize_payment(payload, request=request)


@router.post("/collect", status_code=status.HTTP_201_CREATED)
async def collect_payment(
    request: Request,
    payload: PaymentCollectionRequest,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.collect_payment(payload, request=request)


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_payment(
    payload: PaymentVerificationRequest,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.verify_payment(payload, request=request)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_payment_status(
    reference: str,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.get_payment_status(reference, request=request)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_payment(
    payload: PaymentReconciliationRequest,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.reconcile_payment(payload, request=request)


@router.post("/cancel", status_code=status.HTTP_200_OK)
async def cancel_payment(
    payload: PaymentCancellationRequest,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.cancel_payment(payload, request=request)


@router.get("/history", status_code=status.HTTP_200_OK)
async def get_payment_history(
    payload: PaymentHistoryRequest,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.get_payment_history(payload, request=request)


@router.get("/history/{reference}", status_code=status.HTTP_200_OK)
async def get_payment_details(
    reference: str,
    request: Request,
    controller: PaymentController = Depends(get_payment_controller),
) -> dict[str, Any]:
    return await controller.get_payment_details(reference, request=request)
