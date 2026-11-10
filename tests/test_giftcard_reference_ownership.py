from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.giftcard.trading import GiftCardTradingService
from app.utils.exceptions import ValidationException


class WalletRepository:
    def __init__(self, wallet):
        self.wallet = wallet

    async def get_user_wallet(self, *, user_id):
        return self.wallet if self.wallet.user_id == user_id else None


class TransactionRepository:
    def __init__(self, existing):
        self.existing = existing

    async def get_by_reference(self, reference):
        return self.existing if reference == self.existing.reference else None


class UserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id)


class WalletService:
    async def verify_transaction_pin(self, *, user_id, pin):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "transaction_type", "same_owner"),
    [
        ("buy_giftcard", "giftcard_buy", False),
        ("sell_giftcard", "giftcard_sell", False),
        ("buy_giftcard", "giftcard_sell", True),
    ],
)
async def test_giftcard_reference_reuse_requires_matching_owner_and_type(
    method_name: str,
    transaction_type: str,
    same_owner: bool,
) -> None:
    user_id = uuid4()
    existing = SimpleNamespace(
        reference="giftcard-reference",
        user_id=user_id if same_owner else uuid4(),
        transaction_type=transaction_type,
    )
    wallet = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        available_balance=Decimal("1000.00"),
        is_active=True,
        is_frozen=False,
        is_suspended=False,
    )
    service = GiftCardTradingService(
        wallet_service=WalletService(),
        provider_service=SimpleNamespace(),
        transaction_repository=TransactionRepository(existing),
        user_repository=UserRepository(),
        wallet_repository=WalletRepository(wallet),
    )

    async def provider_operation(_provider):
        raise AssertionError("existing reference must not invoke a provider")

    method = getattr(service, method_name)
    with pytest.raises(ValidationException, match="reference"):
        await method(
            user_id=user_id,
            brand="amazon",
            card_type="ecode",
            amount=Decimal("100.00"),
            transaction_pin="1234",
            reference=existing.reference,
            provider_operation=provider_operation,
        )
