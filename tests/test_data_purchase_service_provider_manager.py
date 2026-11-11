from __future__ import annotations

import json
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
            currency="NGN",
            is_active=True,
            is_suspended=False,
            is_frozen=False,
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


class FakeLedgerService:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def create_debit_entry(self, **payload):
        self.entries.append({"entry_type": "DEBIT", **payload})

    async def create_credit_entry(self, **payload):
        self.entries.append({"entry_type": "CREDIT", **payload})


class FakeQueryResult:
    def __init__(self, transactions=None) -> None:
        self._transactions = transactions if isinstance(transactions, list) else ([transactions] if transactions is not None else [])

    def scalars(self):
        return self

    def all(self):
        return self._transactions


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, SimpleNamespace] = {}
        self.updated: list[dict] = []
        self.executed_queries = []
        self.query_result = None
        self.session = SimpleNamespace(begin=self._begin, execute=self._execute)

    def _begin(self):
        @asynccontextmanager
        async def _ctx():
            yield

        return _ctx()

    async def _execute(self, query):
        self.executed_queries.append(query)
        return FakeQueryResult(self.query_result)

    async def create_transaction(self, transaction):
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
    ledger_service = FakeLedgerService()

    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        ledger_service=ledger_service,
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
    assert len(ledger_service.entries) == 1
    assert ledger_service.entries[0]["entry_type"] == "DEBIT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata_payload", "expected_variant"),
    [
        ('{"service_variant":"awoof"}', "awoof"),
        ('{"service_variant":"awoof_data"}', "awoof"),
        ('{"campaign":"standard"}', None),
    ],
)
async def test_data_purchase_persists_explicit_service_variant(
    metadata_payload: str,
    expected_variant: str | None,
) -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        ledger_service=FakeLedgerService(),
    )

    await service.purchase_data(
        user_id=user_id,
        phone_number="08030000003",
        network="MTN",
        bundle_code="MTN-1GB",
        amount=Decimal("100.00"),
        transaction_pin="1234",
        metadata_payload=metadata_payload,
    )

    transaction = next(iter(transaction_repository.transactions.values()))
    assert json.loads(transaction.metadata_payload).get("service_variant") == expected_variant


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

    result = await service.process_data_purchase(
        transaction=transaction,
        provider_name="requested-provider",
    )

    assert provider_manager.calls[0]["operation"] == "buy_data"
    assert provider_manager.calls[0]["network"] == "GLO"
    assert provider_manager.calls[0]["bundle_code"] == "GLO-2GB"
    assert result["provider"] == "aidapay"
    assert transaction.provider_name == "aidapay"
    assert transaction.provider_reference == "data-ref-001"


@pytest.mark.asyncio
async def test_data_purchase_service_keeps_ambiguous_provider_outcome_pending() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=RuntimeError("provider unavailable"))
    ledger_service = FakeLedgerService()

    service = DataPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        ledger_service=ledger_service,
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

    assert wallet_repository.wallet.available_balance == Decimal("925.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("925.00")
    assert len(ledger_service.entries) == 1
    transaction = next(iter(transaction_repository.transactions.values()))
    assert transaction.status == "pending"
    assert json.loads(transaction.metadata_payload)["provider_outcome_uncertain"] is True
    assert provider_manager.calls[0]["operation"] == "buy_data"


@pytest.mark.asyncio
async def test_successful_data_purchase_does_not_block_a_legitimate_repeat() -> None:
    transaction_repository = FakeTransactionRepository()
    transaction_repository.query_result = SimpleNamespace(
        status="completed",
        metadata_payload=(
            '{"phone_number":"08030000002","network":"AIRTEL",'
            '"bundle_code":"AIRTEL-1GB","requested_amount":"75.00"}'
        ),
    )
    service = DataPurchaseService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    await service._ensure_no_duplicate_purchase(
        user_id=uuid4(),
        amount=Decimal("75.00"),
        phone_number="08030000002",
        network="AIRTEL",
        bundle_code="AIRTEL-1GB",
    )

    query = transaction_repository.executed_queries[-1]
    sql = str(query.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "pending" in sql
    assert "processing" in sql
    assert "succeeded" not in sql
    assert "completed" not in sql
    assert "settled" not in sql
    assert "limit" not in sql
    assert "scalar_one_or_none" not in sql


@pytest.mark.asyncio
async def test_matching_in_flight_data_purchase_is_still_rejected() -> None:
    transaction_repository = FakeTransactionRepository()
    transaction_repository.query_result = SimpleNamespace(
        status="pending",
        metadata_payload=(
            '{"phone_number":"08030000002","network":"AIRTEL",'
            '"bundle_code":"AIRTEL-1GB","requested_amount":"75.00"}'
        )
    )
    service = DataPurchaseService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    with pytest.raises(PaymentException, match="Duplicate data purchase"):
        await service._ensure_no_duplicate_purchase(
            user_id=uuid4(),
            amount=Decimal("75.00"),
            phone_number="08030000002",
            network="AIRTEL",
            bundle_code="AIRTEL-1GB",
        )


@pytest.mark.asyncio
async def test_duplicate_detection_checks_all_in_flight_matching_purchases() -> None:
    transaction_repository = FakeTransactionRepository()
    transaction_repository.query_result = [
        SimpleNamespace(status="pending", metadata_payload='{"phone_number":"08030000003","network":"MTN"}'),
        SimpleNamespace(
            status="processing",
            metadata_payload=(
                '{"phone_number":"08030000002","network":"AIRTEL",'
                '"bundle_code":"AIRTEL-1GB","requested_amount":"75.00"}'
            )
        ),
    ]
    service = DataPurchaseService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
    )

    with pytest.raises(PaymentException, match="Duplicate data purchase"):
        await service._ensure_no_duplicate_purchase(
            user_id=uuid4(),
            amount=Decimal("75.00"),
            phone_number="08030000002",
            network="AIRTEL",
            bundle_code="AIRTEL-1GB",
        )
