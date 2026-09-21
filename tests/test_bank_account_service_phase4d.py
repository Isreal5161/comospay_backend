from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.user.bank_account import BankAccountService
from app.utils.exceptions import ValidationException


class FakeUserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id)


class FakeBankAccountRepository:
    def __init__(self, account) -> None:
        self.account = account
        self.locked_lookup = None

    async def get_by_id_for_user_for_update(self, *, bank_account_id, user_id):
        self.locked_lookup = (bank_account_id, user_id)
        if self.account.id == bank_account_id and self.account.user_id == user_id:
            return self.account
        return None


def make_service(*, status="verified", active=True):
    user_id = uuid4()
    account = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        account_number="0123456789",
        bank_code="044",
        account_name="Trusted Account",
        bank_name="Test Bank",
        is_active=active,
        status=status,
        verified_at=datetime.now(timezone.utc) if status == "verified" else None,
        is_default=False,
        account_type="savings",
        provider_name="flutterwave",
        created_at=datetime.now(timezone.utc),
    )
    repository = FakeBankAccountRepository(account)
    service = BankAccountService(
        user_repository=FakeUserRepository(),
        bank_account_repository=repository,
    )
    return service, repository, user_id, account


@pytest.mark.asyncio
async def test_user_can_resolve_own_verified_bank_account() -> None:
    service, repository, user_id, account = make_service()

    result = await service.resolve_withdrawal_account(user_id=user_id, bank_account_id=account.id)

    assert result["account_number"] == account.account_number
    assert repository.locked_lookup == (account.id, user_id)


@pytest.mark.asyncio
async def test_user_cannot_resolve_another_users_bank_account() -> None:
    service, _, _, account = make_service()

    with pytest.raises(ValidationException, match="not found"):
        await service.resolve_withdrawal_account(user_id=uuid4(), bank_account_id=account.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "active"),
    [("pending", True), ("verified", False)],
)
async def test_unusable_bank_account_is_rejected(status, active) -> None:
    service, _, user_id, account = make_service(status=status, active=active)

    with pytest.raises(ValidationException, match="not verified"):
        await service.resolve_withdrawal_account(user_id=user_id, bank_account_id=account.id)


def test_bank_account_serialization_masks_full_account_number() -> None:
    service, _, _, account = make_service()

    serialized = service._serialize_bank_account(account)

    assert serialized["account_number"] != account.account_number
    assert serialized["account_number"] == "01******89"