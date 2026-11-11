from __future__ import annotations

import json
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
            currency="NGN",
            is_active=True,
            is_frozen=False,
            is_suspended=False,
        )
        self.updated: list[dict] = []

    async def get_by_id(self, wallet_id):
        if wallet_id == self.wallet.id:
            return self.wallet
        return None

    async def get_by_id_for_update(self, wallet_id):
        return await self.get_by_id(wallet_id)

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


class FakeLedgerService:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def create_debit_entry(self, **payload):
        self.entries.append({"entry_type": "DEBIT", **payload})

    async def create_credit_entry(self, **payload):
        self.entries.append({"entry_type": "CREDIT", **payload})


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, SimpleNamespace] = {}
        self.updated: list[dict] = []

        class _Session:
            active = False

            def in_transaction(self_inner):
                return self_inner.active

            def begin(self_inner):
                @asynccontextmanager
                async def _ctx():
                    previous = self_inner.active
                    self_inner.active = True
                    try:
                        yield
                    finally:
                        self_inner.active = previous

                return _ctx()

        self.session = _Session()

    async def create_transaction(self, transaction):
        if transaction.id is None:
            transaction.id = uuid4()
        self.transactions[transaction.reference] = transaction
        return transaction

    async def get_by_reference(self, reference):
        return self.transactions.get(reference)

    async def get_by_reference_for_update(self, reference):
        return await self.get_by_reference(reference)

    async def update_transaction(self, transaction, **kwargs):
        self.updated.append({"reference": transaction.reference, **kwargs})
        for key, value in kwargs.items():
            setattr(transaction, key, value)
        return transaction


class FakeProviderManager:
    def __init__(self, response=None, error: Exception | None = None, on_execute=None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._error = error
        self.on_execute = on_execute

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        if self.on_execute is not None:
            await self.on_execute()
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
    ledger_service = FakeLedgerService()

    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )
    async def verify_initial_state():
        created = next(iter(transaction_repository.transactions.values()))
        assert created.status == "pending"
        assert json.loads(created.metadata_payload)["provider_attempted"] is True
        assert wallet_repository.wallet.available_balance == Decimal("800.00")
        assert ledger_service.entries[0]["entry_type"] == "DEBIT"

    provider_manager.on_execute = verify_initial_state

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
        ledger_service=FakeLedgerService(),
    )

    result = await service.process_electricity_purchase(
        transaction=transaction,
        provider_name="requested-provider",
    )

    assert provider_manager.calls[0]["operation"] == "purchase_electricity"
    assert provider_manager.calls[0]["provider"] == "EKEDC"
    assert result["provider"] == "aidapay"
    assert transaction.provider_name == "aidapay"
    assert transaction.provider_reference == "elec-ref-001"
    assert transaction.provider_transaction_id == "elec-tx-001"


@pytest.mark.asyncio
async def test_electricity_purchase_service_keeps_wallet_debit_pending_for_reconciliation() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=RuntimeError("provider unavailable"))

    ledger_service = FakeLedgerService()
    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )

    with pytest.raises(PaymentException, match="outcome could not be confirmed") as exc_info:
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
    transaction = next(iter(transaction_repository.transactions.values()))
    assert transaction.status == "pending"
    metadata = json.loads(transaction.metadata_payload)
    assert metadata["provider_outcome_uncertain"] is True
    assert metadata["provider_error"] == "provider outcome could not be confirmed"
    assert "provider unavailable" not in metadata["provider_error"]
    assert "provider unavailable" not in str(exc_info.value)
    assert metadata["debit_applied"] is True
    assert len(ledger_service.entries) == 1


@pytest.mark.asyncio
async def test_electricity_definitive_provider_failure_response_restores_wallet_debit() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(response={"status": "failed", "message": "declined"})
    ledger_service = FakeLedgerService()
    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )

    result = await service.purchase_electricity(
        user_id=user_id,
        meter_number="12345678903",
        disco="PHED",
        amount=Decimal("125.00"),
        transaction_pin="1234",
    )

    transaction = next(iter(transaction_repository.transactions.values()))
    assert result["status"] == "failed"
    assert wallet_repository.wallet.available_balance == Decimal("1000.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("1000.00")
    assert json.loads(transaction.metadata_payload)["reversal_applied"] is True
    assert [entry["entry_type"] for entry in ledger_service.entries] == ["DEBIT", "CREDIT"]


@pytest.mark.asyncio
async def test_electricity_reversal_is_idempotent() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    wallet_repository.wallet.available_balance = Decimal("875.00")
    wallet_repository.wallet.ledger_balance = Decimal("875.00")
    transaction_repository = FakeTransactionRepository()
    transaction = SimpleNamespace(
        id=uuid4(),
        reference="elec-reversal",
        user_id=user_id,
        wallet_id=wallet_repository.wallet.id,
        transaction_type="electricity_purchase",
        category="electricity",
        amount=Decimal("125.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("125.00"),
        status="pending",
        provider_name=None,
        provider_reference=None,
        provider_transaction_id=None,
        external_reference=None,
        description="Electricity purchase",
        metadata_payload=json.dumps({"debit_applied": True}),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    transaction_repository.transactions[transaction.reference] = transaction
    ledger_service = FakeLedgerService()
    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )

    await service.reverse_electricity_purchase(reference=transaction.reference)
    await service.reverse_electricity_purchase(reference=transaction.reference)

    assert wallet_repository.wallet.available_balance == Decimal("1000.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("1000.00")
    assert transaction.status == "reversed"
    assert json.loads(transaction.metadata_payload)["debit_applied"] is False
    assert [entry["entry_type"] for entry in ledger_service.entries] == ["CREDIT"]


@pytest.mark.asyncio
async def test_electricity_uncertain_purchase_cannot_be_redispatched() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=RuntimeError("provider timeout"))
    service = ElectricityPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=FakeLedgerService(),
    )

    with pytest.raises(PaymentException):
        await service.purchase_electricity(
            user_id=user_id,
            meter_number="12345678903",
            disco="IKEDC",
            amount=Decimal("125.00"),
            transaction_pin="1234",
        )
    transaction = next(iter(transaction_repository.transactions.values()))

    with pytest.raises(PaymentException, match="pending reconciliation"):
        await service.retry_electricity_purchase(transaction=transaction)

    assert len(provider_manager.calls) == 1
