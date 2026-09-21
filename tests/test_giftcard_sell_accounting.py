from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.provider import Provider
from app.services.giftcard.trading import GiftCardTradingService
from app.utils.exceptions import ValidationException, WalletException


class WalletRepository:
    async def update_balance_fields(self, wallet, **fields):
        for field, value in fields.items():
            setattr(wallet, field, value)


class TransactionRepository:
    async def update_transaction(self, transaction, **fields):
        for field, value in fields.items():
            setattr(transaction, field, value)
        return transaction


def service() -> GiftCardTradingService:
    return GiftCardTradingService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        transaction_repository=TransactionRepository(),
        user_repository=SimpleNamespace(),
        wallet_repository=WalletRepository(),
    )


def transaction(status: str = "succeeded") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        reference="giftcard-sell-test",
        transaction_type="giftcard_sell",
        category="giftcard",
        amount=Decimal("100.00"),
        currency="USD",
        card_amount=Decimal("100.00"),
        card_currency="USD",
        payout_amount=Decimal("125000.00"),
        payout_currency="NGN",
        credited_amount=None,
        credited_currency=None,
        wallet_id=uuid4(),
        user_id=uuid4(),
        provider_name="Sogo",
        provider_reference="SOGO-REF",
        provider_transaction_id="SOGO-TX",
        status=status,
        metadata_payload="{}",
        description="Gift card sell",
        created_at=None,
        updated_at=None,
    )


def wallet(currency: str = "NGN") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        currency=currency,
        available_balance=Decimal("0.00"),
        ledger_balance=Decimal("0.00"),
    )


@pytest.mark.asyncio
async def test_completed_sell_credits_exact_provider_payout() -> None:
    trading = service()
    item = transaction()
    target = wallet()

    async def get_wallet(_transaction, *, for_update=False):
        return target

    trading._get_wallet_for_transaction = get_wallet

    await trading._handle_successful_trade(item)

    assert target.available_balance == Decimal("125000.00")
    assert target.ledger_balance == Decimal("125000.00")
    assert target.available_balance != item.amount
    assert item.card_amount == Decimal("100.00")
    assert item.card_currency == "USD"
    assert item.payout_amount == Decimal("125000.00")
    assert item.payout_currency == "NGN"
    assert item.credited_amount == Decimal("125000.00")
    assert item.credited_currency == "NGN"


@pytest.mark.asyncio
async def test_sell_payout_currency_mismatch_is_rejected_without_credit() -> None:
    trading = service()
    item = transaction()
    target = wallet(currency="USD")

    async def get_wallet(_transaction, *, for_update=False):
        return target

    trading._get_wallet_for_transaction = get_wallet

    with pytest.raises(WalletException, match="currency"):
        await trading._handle_successful_trade(item)

    assert target.available_balance == Decimal("0.00")
    assert item.credited_amount is None


def test_provider_normalization_preserves_non_sensitive_payout_values() -> None:
    provider = Provider(
        id=uuid4(),
        code="sogo",
        name="Sogo",
        category="Gift Cards",
        status="active",
        is_active=True,
        environment="production",
    )
    normalized = service()._normalize_provider_response(
        {
            "status": "completed",
            "provider_reference": "SOGO-REF",
            "provider_transaction_id": "SOGO-TX",
            "payout_amount": {"raw": 125000, "currency": "NGN"},
            "card_amount": 100,
            "card_currency": "USD",
        },
        provider,
    )

    assert normalized["payout_amount"] == {"raw": 125000, "currency": "NGN"}
    assert normalized["payout_currency"] == "NGN"
    assert normalized["card_amount"] == 100
    assert normalized["card_currency"] == "USD"
    assert normalized["provider_reference"] == "SOGO-REF"
    assert normalized["provider_transaction_id"] == "SOGO-TX"


def test_buy_transaction_semantics_remain_generic() -> None:
    item = transaction()
    item.transaction_type = "giftcard_buy"
    item.amount = Decimal("100.00")
    item.currency = "NGN"
    assert item.amount == Decimal("100.00")
    assert item.currency == "NGN"


def test_missing_sell_payout_is_rejected() -> None:
    trading = service()
    item = transaction()
    item.payout_amount = None
    item.payout_currency = None
    target = wallet()
    async def get_wallet(_transaction, *, for_update=False):
        return target

    trading._get_wallet_for_transaction = get_wallet

    with pytest.raises(ValidationException, match="payout"):
        trading._validate_sell_payout(item, target)
