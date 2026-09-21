from __future__ import annotations

"""Gift Card route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.giftcard_controller import (
    GiftCardController,
    GiftCardCountryRequest,
    GiftCardHistoryRequest,
    GiftCardInitiationRequest,
    GiftCardPricingRequest,
    GiftCardReconciliationRequest,
    GiftCardSettlementRequest,
    GiftCardStatusRequest,
    GiftCardSupportedRequest,
    GiftCardTradeRequest,
    GiftCardValuationRequest,
)
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.giftcard.pricing import GiftCardPricingService
from app.services.giftcard.reconciliation import GiftCardReconciliationService
from app.services.giftcard.settlement import GiftCardSettlementService
from app.services.giftcard.trading import GiftCardTradingService
from app.services.giftcard.valuation import GiftCardValuationService
from app.services.giftcard_service import GiftCardService
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
from app.integrations.giftcards.sogo import SogoGiftCardProvider
from app.models.provider import Provider

router = APIRouter(prefix="/giftcards", tags=["Gift Cards"])


def build_provider_integration(provider: Provider) -> Any:
    """Instantiate the gift card provider integration for the resolved provider.

    This factory function maps each provider to its corresponding gift card adapter.
    Currently supports: Sogo (Phase 3B integration).
    """
    provider_code = (provider.code or provider.name or "").strip().lower()

    if provider_code == "sogo":
        return SogoGiftCardProvider()

    # Future providers would be added here:
    # if provider_code == "cardtonic":
    #     return CardtonicGiftCardProvider()
    # if provider_code == "prestmit":
    #     return PrestmitGiftCardProvider()

    raise ValueError(f"Unsupported gift card provider: {provider_code}")


async def get_giftcard_service(session: AsyncSession = Depends(get_db)) -> GiftCardService:
    """Compose a request-scoped gift card service graph from the active database session."""
    provider_repository = ProviderRepository(session=session)
    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)
    transaction_repository = TransactionRepository(session=session)
    settings_repository = SystemSettingsRepository(session=session)

    selector = ProviderSelector(provider_repository=provider_repository, redis_client=None)
    health_service = ProviderHealthService(provider_repository=provider_repository, redis_client=None)
    failover_service = ProviderFailoverService(selector=selector, health_service=health_service)
    provider_service = ProviderService(
        selector=selector,
        health_service=health_service,
        failover_service=failover_service,
        provider_repository=provider_repository,
    )

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
    wallet_service = WalletService(
        wallet_manager=wallet_manager,
        funding_service=funding_service,
        transfer_service=transfer_service,
        pin_service=pin_service,
        statement_service=statement_service,
    )

    trading_service = GiftCardTradingService(
        wallet_service=wallet_service,
        provider_service=provider_service,
        transaction_repository=transaction_repository,
        user_repository=user_repository,
        wallet_repository=wallet_repository,
    )
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
        provider_integration_builder=build_provider_integration,
    )


async def get_giftcard_controller(service: GiftCardService = Depends(get_giftcard_service)) -> GiftCardController:
    """Instantiate the gift card controller with a request-scoped service graph."""
    return GiftCardController(service)


@router.post("/valuation", status_code=status.HTTP_200_OK)
async def valuate_giftcard(payload: GiftCardValuationRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.valuate_giftcard(payload)


@router.post("/trade", status_code=status.HTTP_201_CREATED)
async def trade_giftcard(request: Request, payload: GiftCardTradeRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.trade_giftcard(payload, request=request)


@router.post("/pricing", status_code=status.HTTP_200_OK)
async def get_pricing(payload: GiftCardPricingRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.get_pricing(payload)


@router.post("/initiate", status_code=status.HTTP_201_CREATED)
async def initiate_transaction(payload: GiftCardInitiationRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.initiate_transaction(payload)


@router.post("/settle", status_code=status.HTTP_200_OK)
async def settle_transaction(payload: GiftCardSettlementRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.settle_transaction(payload)


@router.post("/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_transaction(
    payload: GiftCardReconciliationRequest,
    request: Request,
    controller: GiftCardController = Depends(get_giftcard_controller),
) -> dict[str, Any]:
    return await controller.reconcile_transaction(payload, request=request)


@router.get("/supported", status_code=status.HTTP_200_OK)
async def get_supported_cards(payload: GiftCardSupportedRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.get_supported_cards(payload)


@router.get("/countries", status_code=status.HTTP_200_OK)
async def get_supported_countries(payload: GiftCardCountryRequest, controller: GiftCardController = Depends(get_giftcard_controller)) -> dict[str, Any]:
    return await controller.get_supported_countries(payload)


@router.get("/status/{reference}", status_code=status.HTTP_200_OK)
async def get_trade_status(
    reference: str,
    request: Request,
    controller: GiftCardController = Depends(get_giftcard_controller),
) -> dict[str, Any]:
    return await controller.get_trade_status(reference, request=request)


@router.post("/history", status_code=status.HTTP_200_OK)
async def get_trade_history(
    payload: GiftCardHistoryRequest,
    request: Request,
    controller: GiftCardController = Depends(get_giftcard_controller),
) -> dict[str, Any]:
    return await controller.get_trade_history(payload, request=request)


@router.get("/details/{reference}", status_code=status.HTTP_200_OK)
async def get_trade_details(
    reference: str,
    request: Request,
    controller: GiftCardController = Depends(get_giftcard_controller),
) -> dict[str, Any]:
    return await controller.get_trade_details(reference, request=request)