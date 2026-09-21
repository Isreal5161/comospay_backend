from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.giftcard.trading import GiftCardTradingService
from app.utils.exceptions import WalletException


class WalletRepository:
    async def update_balance_fields(self, wallet, **fields):
        for field, value in fields.items():
            setattr(wallet, field, value)


class TransactionRepository:
    async def update_transaction(self, transaction, **fields):
        for field, value in fields.items():
            setattr(transaction, field, value)
        return transaction


def make_service() -> GiftCardTradingService:
    return GiftCardTradingService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        transaction_repository=TransactionRepository(),
        user_repository=SimpleNamespace(),
        wallet_repository=WalletRepository(),
    )


def make_transaction() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        reference="sell-idempotency-test",
        transaction_type="giftcard_sell",
        amount=Decimal("100.00"),
        currency="USD",
        payout_amount=Decimal("125000.00"),
        payout_currency="NGN",
        credited_amount=None,
        credited_currency=None,
        credit_applied=False,
        wallet_id=uuid4(),
        metadata_payload="{}",
    )


def make_wallet(currency: str = "NGN") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        currency=currency,
        available_balance=Decimal("1000000.00"),
        ledger_balance=Decimal("1000000.00"),
    )


@pytest.mark.asyncio
async def test_repeated_completed_sell_credits_only_once() -> None:
    service = make_service()
    transaction = make_transaction()
    wallet = make_wallet()

    async def get_wallet(_transaction, *, for_update=False):
        assert for_update is True
        return wallet

    service._get_wallet_for_transaction = get_wallet

    await service._handle_successful_trade(transaction)
    await service._handle_successful_trade(transaction)

    assert wallet.available_balance == Decimal("1125000.00")
    assert wallet.ledger_balance == Decimal("1125000.00")
    assert transaction.credit_applied is True
    assert transaction.credited_amount == Decimal("125000.00")
    assert transaction.credited_currency == "NGN"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "failed", "cancelled"])
async def test_non_successful_sell_does_not_credit(status: str) -> None:
    service = make_service()
    transaction = make_transaction()
    transaction.status = status
    wallet = make_wallet()

    assert wallet.available_balance == Decimal("1000000.00")
    assert transaction.credit_applied is False


@pytest.mark.asyncio
async def test_currency_mismatch_does_not_credit() -> None:
    service = make_service()
    transaction = make_transaction()
    wallet = make_wallet(currency="USD")

    async def get_wallet(_transaction, *, for_update=False):
        return wallet

    service._get_wallet_for_transaction = get_wallet

    with pytest.raises(WalletException):
        await service._handle_successful_trade(transaction)

    assert wallet.available_balance == Decimal("1000000.00")
    assert transaction.credit_applied is False
