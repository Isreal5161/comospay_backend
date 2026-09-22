import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.wallet.withdrawal import WalletWithdrawalService
from app.utils.exceptions import ValidationException


class DummyRepository(SimpleNamespace):
    pass


class WithdrawalWalletRepository:
    def __init__(self, wallet) -> None:
        self.wallet = wallet
        self.lock_calls = 0

    async def get_user_wallet_for_update(self, *, user_id):
        self.lock_calls += 1
        return self.wallet if self.wallet.user_id == user_id else None

    async def update_balance_fields(self, wallet, **fields):
        for field, value in fields.items():
            setattr(wallet, field, value)
        return wallet


class WithdrawalTransactionRepository:
    def __init__(self) -> None:
        self.transactions = []

    async def create_transaction(self, transaction):
        transaction.id = transaction.id or uuid4()
        self.transactions.append(transaction)
        return transaction


class WithdrawalBankAccountService:
    def __init__(self, user_id) -> None:
        self.user_id = user_id
        self.bank_account_id = uuid4()

    async def resolve_withdrawal_account(self, *, user_id, bank_account_id):
        assert user_id == self.user_id
        assert bank_account_id == self.bank_account_id
        return {"account_number": "0123456789", "bank_code": "044"}


def make_withdrawal_service():
    user_id = uuid4()
    wallet = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        currency="NGN",
        available_balance=Decimal("125000.00"),
        ledger_balance=Decimal("125000.00"),
        locked_balance=Decimal("0.00"),
        is_frozen=False,
        is_suspended=False,
        is_active=True,
    )
    wallet_repository = WithdrawalWalletRepository(wallet)
    transaction_repository = WithdrawalTransactionRepository()
    bank_account_service = WithdrawalBankAccountService(user_id)
    service = WalletWithdrawalService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        bank_account_service=bank_account_service,
        provider_service=SimpleNamespace(
            withdraw_to_bank=lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider called")),
        ),
    )
    return service, wallet_repository, transaction_repository, user_id, wallet, bank_account_service


def test_validate_transaction_pin_accepts_numeric_pin() -> None:
    service = WalletWithdrawalService(
        wallet_repository=DummyRepository(),
        transaction_repository=DummyRepository(),
    )

    assert asyncio.run(service.validate_transaction_pin(transaction_pin="123456")) is True


def test_validate_transaction_pin_rejects_non_numeric_pin() -> None:
    service = WalletWithdrawalService(
        wallet_repository=DummyRepository(),
        transaction_repository=DummyRepository(),
    )

    with pytest.raises(ValidationException):
        asyncio.run(service.validate_transaction_pin(transaction_pin="abcd"))


def test_create_withdrawal_reserves_funds_without_provider_call() -> None:
    service, wallet_repository, transaction_repository, user_id, wallet, bank_account_service = make_withdrawal_service()

    result = asyncio.run(
        service.create_withdrawal(
            user_id=user_id,
            amount=Decimal("100000.00"),
            currency="ngn",
            bank_account_id=bank_account_service.bank_account_id,
            metadata_payload='{"account_number":"9999999999"}',
        )
    )

    assert result["status"] == "funds_reserved"
    assert wallet.available_balance == Decimal("25000.00")
    assert wallet.locked_balance == Decimal("100000.00")
    assert wallet_repository.lock_calls == 1
    assert len(transaction_repository.transactions) == 1
    assert transaction_repository.transactions[0].bank_account_id == bank_account_service.bank_account_id
    assert "9999999999" not in transaction_repository.transactions[0].metadata_payload
    assert transaction_repository.transactions[0].provider_reference is None


def test_withdrawal_references_are_unique() -> None:
    service, _, transaction_repository, user_id, _, bank_account_service = make_withdrawal_service()

    first = asyncio.run(
        service.create_withdrawal(
            user_id=user_id,
            amount=Decimal("10.00"),
            currency="NGN",
            bank_account_id=bank_account_service.bank_account_id,
        )
    )
    transaction_repository.transactions.clear()
    service.wallet_repository.wallet.available_balance = Decimal("125000.00")
    second = asyncio.run(
        service.create_withdrawal(
            user_id=user_id,
            amount=Decimal("10.00"),
            currency="NGN",
            bank_account_id=bank_account_service.bank_account_id,
        )
    )

    assert first["reference"] != second["reference"]
