from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.data.purchase import DataPurchaseService
from app.utils.exceptions import PaymentException


class FakeWalletRepository:
    def __init__(self) -> None:
        self.wallet = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            available_balance=Decimal("1000.00"),
            ledger_balance=Decimal("1000.00"),
            is_active=True,
            is_suspended=False,
            is_frozen=False,
        )
        self.updated: list[dict] = []

    async def get_by_id(self, wallet_id):
        if wallet_id == self.wallet.id:
            return self.wallet
        return None

    async def get_user_wallet(self, *, user_id):
        if user_id == self.wallet.user_id:
            return self.wallet
        return None

    async def update_balance_fields(self, wallet, **kwargs):
        self.updated.append(kwargs)
        return wallet


class FakeWalletManager:
    def __init__(self, wallet_repository: FakeWalletRepository) -> None:
        self.wallet_repository = wallet_repository


class FakeWalletService:
    def __init__(self, wallet_repository: FakeWalletRepository) -> None:
        self.wallet_manager = FakeWalletManager(wallet_repository)

    async def verify_transaction_pin(self, *, user_id, pin):
        return None


class FakeUserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id)


class FakeQueryResult:
    def __init__(self, transaction=None) -> None:
        self._transaction = transaction

    def scalar_one_or_none(self):
        return self._transaction


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, SimpleNamespace] = {}
        self.updated: list[dict] = []
        self.session = SimpleNamespace(begin=self._begin, execute=self._execute)

    def _begin(self):
        @asynccontextmanager
        async def _ctx():
            yield

        return _ctx()

    async def _execute(self, query):
        return FakeQueryResult()

    async def create_transaction(self, transaction):
        self.transactions[transaction.reference] = transaction
        return transaction

    async def get_by_reference(self, reference):
        return self.transactions.get(reference)

    async def update_transaction(self, transaction, **kwargs):
        self.updated.append({"reference": transaction.reference, **kwargs})
        for key, value in kwargs.items():
            setattr(transaction, key, value)
        return transaction


class FakeProviderManager:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._error = error

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return {
            "provider": "aidapay",
            "data": {
                "success": True,
                "message": "processed",
                "data": {
                    "transaction_data": {
                        "status": "success",
                        "provider": "aidapay",
                        "transaction_hash": "data-ref-001",
                        "transaction_id": "data-tx-001",
                    }
                },
            },
        }


@pytest.mark.asyncio
async def test_data_purchase_service_executes_provider_manager_buy_data() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()

    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    result = await service.purchase_data(
        user_id=user_id,
        phone_number="08030000000",
        network="MTN",
        bundle_code="MTN-1GB",
        amount=Decimal("100.00"),
        transaction_pin="1234",
    )

    assert provider_manager.calls[0]["operation"] == "buy_data"
    assert provider_manager.calls[0]["phone_number"] == "08030000000"
    assert provider_manager.calls[0]["network"] == "MTN"
    assert provider_manager.calls[0]["bundle_code"] == "MTN-1GB"
    assert result["status"] == "succeeded"
    assert result["provider"] == "aidapay"
    assert result["provider_reference"] == "data-ref-001"
    assert result["provider_transaction_id"] == "data-tx-001"
    assert wallet_repository.wallet.available_balance == Decimal("900.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("900.00")


@pytest.mark.asyncio
async def test_data_purchase_service_processes_existing_transaction_with_provider_manager() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()
    transaction = SimpleNamespace(
        reference="data-001",
        user_id=user_id,
        wallet_id=wallet_repository.wallet.id,
        transaction_type="data_purchase",
        category="data",
        amount=Decimal("50.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("50.00"),
        status="pending",
        provider_name=None,
        provider_reference=None,
        provider_transaction_id=None,
        external_reference=None,
        description="Data purchase",
        metadata_payload='{"phone_number":"08030000001","network":"GLO","bundle_code":"GLO-2GB","requested_amount":"50.00"}',
    )
    transaction_repository.transactions[transaction.reference] = transaction

    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    result = await service.process_data_purchase(transaction=transaction)

    assert provider_manager.calls[0]["operation"] == "buy_data"
    assert provider_manager.calls[0]["network"] == "GLO"
    assert provider_manager.calls[0]["bundle_code"] == "GLO-2GB"
    assert result["provider"] == "aidapay"
    assert transaction.provider_name == "aidapay"
    assert transaction.provider_reference == "data-ref-001"


@pytest.mark.asyncio
async def test_data_purchase_service_reverses_wallet_on_provider_failure() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=RuntimeError("provider unavailable"))

    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    with pytest.raises(PaymentException):
        await service.purchase_data(
            user_id=user_id,
            phone_number="08030000002",
            network="AIRTEL",
            bundle_code="AIRTEL-1GB",
            amount=Decimal("75.00"),
            transaction_pin="1234",
        )

    assert wallet_repository.wallet.available_balance == Decimal("1000.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("1000.00")
    assert provider_manager.calls[0]["operation"] == "buy_data"
