from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, UTC
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.electricity.purchase import ElectricityPurchaseService
from app.utils.exceptions import PaymentException


class FakeWalletRepository:
    def __init__(self) -> None:
        self.wallet = SimpleNamespace(
            id=uuid4(),
            user_id=uuid4(),
            available_balance=Decimal("1000.00"),
            ledger_balance=Decimal("1000.00"),
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


class FakeWalletService:
    def __init__(self, wallet_repository: FakeWalletRepository) -> None:
        self.wallet_repository = wallet_repository

    async def verify_transaction_pin(self, *, user_id, pin):
        return None


class FakeUserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id)


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, SimpleNamespace] = {}
        self.updated: list[dict] = []

        class _Session:
            def begin(self_inner):
                @asynccontextmanager
                async def _ctx():
                    yield

                return _ctx()

        self.session = _Session()

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
                        "transaction_hash": "elec-ref-001",
                        "transaction_id": "elec-tx-001",
                        "token": "TOKEN-123",
                        "receipt": "RCPT-123",
                    }
                },
            },
        }


@pytest.mark.asyncio
async def test_electricity_purchase_service_executes_provider_manager_purchase_electricity() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()

    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
    )

    result = await service.purchase_electricity(
        user_id=user_id,
        meter_number="12345678901",
        disco="IKEDC",
        amount=Decimal("200.00"),
        transaction_pin="1234",
    )

    assert provider_manager.calls[0]["operation"] == "purchase_electricity"
    assert provider_manager.calls[0]["meter_number"] == "12345678901"
    assert provider_manager.calls[0]["provider"] == "IKEDC"
    assert provider_manager.calls[0]["amount"] == "200.00"
    assert result["status"] == "succeeded"
    assert result["provider"] == "aidapay"
    assert result["provider_reference"] == "elec-ref-001"
    assert result["provider_transaction_id"] == "elec-tx-001"
    assert wallet_repository.wallet.available_balance == Decimal("800.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("800.00")


@pytest.mark.asyncio
async def test_electricity_purchase_service_processes_existing_transaction_with_provider_manager() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()
    transaction = SimpleNamespace(
        reference="elec-001",
        user_id=user_id,
        wallet_id=wallet_repository.wallet.id,
        transaction_type="electricity_purchase",
        category="electricity",
        amount=Decimal("150.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("150.00"),
        status="pending",
        provider_name=None,
        provider_reference=None,
        provider_transaction_id=None,
        external_reference=None,
        description="Electricity purchase",
        metadata_payload='{"meter_number":"12345678902","disco":"EKEDC"}',
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    transaction_repository.transactions[transaction.reference] = transaction

    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
    )

    result = await service.process_electricity_purchase(transaction=transaction)

    assert provider_manager.calls[0]["operation"] == "purchase_electricity"
    assert provider_manager.calls[0]["provider"] == "EKEDC"
    assert result["provider"] == "aidapay"
    assert transaction.provider_name == "aidapay"
    assert transaction.provider_reference == "elec-ref-001"
    assert transaction.provider_transaction_id == "elec-tx-001"


@pytest.mark.asyncio
async def test_electricity_purchase_service_reverses_wallet_on_provider_failure() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=RuntimeError("provider unavailable"))

    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
    )

    with pytest.raises(PaymentException):
        await service.purchase_electricity(
            user_id=user_id,
            meter_number="12345678903",
            disco="PHED",
            amount=Decimal("125.00"),
            transaction_pin="1234",
        )

    assert wallet_repository.wallet.available_balance == Decimal("875.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("875.00")
    assert provider_manager.calls[0]["operation"] == "purchase_electricity"
