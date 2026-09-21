from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.routes.notification_routes import build_notification_service
from app.controllers.wallet_controller import (
    TransactionDetailRequest,
    TransactionHistoryRequest,
    TransactionPinRequest,
    TransactionPinResetRequest,
    TransactionPinVerificationRequest,
    WalletBalanceRequest,
    WalletController,
    WalletFundingRequest,
    WalletInfoRequest,
    WalletStatementRequest,
    WalletTransferRequest,
    WalletWithdrawalRequest,
)
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.bank_account_repository import BankAccountRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.user.bank_account import BankAccountService
from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
    WalletWithdrawalService,
)
from app.services.wallet_service import WalletService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService


router = APIRouter(prefix="/wallets", tags=["Wallets"])


def __import_virtual_account_service(session, user_repository, wallet_repository):
    from app.repositories.virtual_account_repository import VirtualAccountRepository
    from app.services.virtual_account_service import VirtualAccountService
    from app.integrations.payments.flutterwave.client import FlutterwaveClient
    from app.integrations.payments.flutterwave.virtual_accounts import FlutterwaveVirtualAccountService

    virtual_account_repository = VirtualAccountRepository(session=session)
    flutterwave_client = FlutterwaveClient()
    flutterwave_va = FlutterwaveVirtualAccountService(client=flutterwave_client)
    provider_map = {"flutterwave": flutterwave_va}

    return VirtualAccountService(
        virtual_account_repository=virtual_account_repository,
        wallet_repository=wallet_repository,
        user_repository=user_repository,
        provider_services=provider_map,
        notification_service=build_notification_service(session=session, redis_client=None),
        session=session,
    )


async def get_wallet_service(session: AsyncSession = Depends(get_db)) -> WalletService:
    """Compose the wallet service graph per request using the active database session."""
    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)
    transaction_repository = TransactionRepository(session=session)
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

    from app.integrations.payments.flutterwave.withdrawal import build_flutterwave_withdrawal_provider

    flutterwave_withdrawal = build_flutterwave_withdrawal_provider()

    def withdrawal_provider_factory(provider: Any) -> Any:
        provider_code = (getattr(provider, "code", None) or getattr(provider, "name", None) or "").strip().lower()
        if provider_code == "flutterwave":
            return flutterwave_withdrawal
        raise ValueError(f"Unsupported withdrawal provider: {provider_code}")
    bank_account_service = BankAccountService(
        user_repository=user_repository,
        bank_account_repository=BankAccountRepository(session=session),
        session=session,
    )

    wallet_manager = WalletManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        # VirtualAccountService must be provided per new business requirement
        virtual_account_service=__import_virtual_account_service(session, user_repository, wallet_repository),
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
    withdrawal_service = WalletWithdrawalService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        bank_account_service=bank_account_service,
        provider_service=provider_service,
        provider_adapter_factory=withdrawal_provider_factory,
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
        withdrawal_service=withdrawal_service,
    )


async def get_wallet_controller(wallet_service: WalletService = Depends(get_wallet_service)) -> WalletController:
    """Instantiate the wallet controller with a request-scoped wallet service."""
    return WalletController(wallet_service)


@router.get("", status_code=status.HTTP_200_OK)
async def get_wallet(
    request: Request,
    payload: WalletInfoRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.get_wallet(payload, request=request)


@router.get("/balance", status_code=status.HTTP_200_OK)
async def get_wallet_balance(
    request: Request,
    payload: WalletBalanceRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.get_wallet_balance(payload, request=request)


@router.get("/statement", status_code=status.HTTP_200_OK)
async def get_wallet_statement(
    request: Request,
    payload: WalletStatementRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.get_wallet_statement(payload, request=request)


@router.post("/fund", status_code=status.HTTP_200_OK)
async def fund_wallet(
    request: Request,
    payload: WalletFundingRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.fund_wallet(payload, request=request)


@router.post("/transfer", status_code=status.HTTP_200_OK)
async def transfer(
    request: Request,
    payload: WalletTransferRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.transfer(payload, request=request)


@router.post("/withdraw", status_code=status.HTTP_201_CREATED)
async def withdraw(
    request: Request,
    payload: WalletWithdrawalRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.withdraw(payload, request=request)


@router.post("/pin", status_code=status.HTTP_201_CREATED)
async def create_transaction_pin(
    request: Request,
    payload: TransactionPinRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.create_transaction_pin(payload, request=request)


@router.put("/pin", status_code=status.HTTP_200_OK)
async def update_transaction_pin(
    request: Request,
    payload: TransactionPinRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.update_transaction_pin(payload, request=request)


@router.post("/pin/verify", status_code=status.HTTP_200_OK)
async def verify_transaction_pin(
    request: Request,
    payload: TransactionPinVerificationRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.verify_transaction_pin(payload, request=request)


@router.post("/pin/reset", status_code=status.HTTP_200_OK)
async def reset_transaction_pin(
    request: Request,
    payload: TransactionPinResetRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.reset_transaction_pin(payload, request=request)


@router.get("/transactions", status_code=status.HTTP_200_OK)
async def get_transaction_history(
    request: Request,
    payload: TransactionHistoryRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.get_transaction_history(payload, request=request)


@router.get("/transactions/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_transaction_details(
    transaction_id: str,
    request: Request,
    payload: TransactionDetailRequest,
    controller: WalletController = Depends(get_wallet_controller),
) -> dict[str, Any]:
    return await controller.get_transaction_details(UUID(transaction_id), payload, request=request)
