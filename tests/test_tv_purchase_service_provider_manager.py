from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError
from app.services.tv.purchase import TVPurchaseService
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
        if getattr(transaction, "created_at", None) is None:
            transaction.created_at = datetime.now(UTC)
        if getattr(transaction, "updated_at", None) is None:
            transaction.updated_at = datetime.now(UTC)
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
                        "transaction_hash": "tv-ref-001",
                        "transaction_id": "tv-tx-001",
                        "token": "TOKEN-001",
                        "receipt": "RCPT-001",
                    }
                },
            },
        }


@pytest.mark.asyncio
async def test_tv_purchase_service_executes_provider_manager_subscribe_tv() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()
    ledger_service = FakeLedgerService()

    service = TVPurchaseService(
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
        assert wallet_repository.wallet.available_balance == Decimal("750.00")
        assert ledger_service.entries[0]["entry_type"] == "DEBIT"

    provider_manager.on_execute = verify_initial_state

    result = await service.purchase_tv(
        user_id=user_id,
        provider="DSTV",
        smart_card_number="1234567890",
        amount=Decimal("250.00"),
        transaction_pin="1234",
        package_code="DTV-1",
        service_type="COMPACT",
        provider_name="requested-provider",
    )

    assert provider_manager.calls[0]["operation"] == "subscribe_tv"
    assert provider_manager.calls[0]["smart_card_number"] == "1234567890"
    assert provider_manager.calls[0]["provider_code"] == "DSTV"
    assert provider_manager.calls[0]["package"] == "COMPACT"
    assert provider_manager.calls[0]["package_code"] == "DTV-1"
    assert provider_manager.calls[0]["amount"] == "250.00"
    assert result["status"] == "succeeded"
    assert result["provider_name"] == "aidapay"
    assert result["provider_reference"] == "tv-ref-001"
    assert result["provider_transaction_id"] == "tv-tx-001"

    created = transaction_repository.transactions[result["reference"]]
    assert created.provider_name == "aidapay"
    metadata = json.loads(created.metadata_payload)
    assert metadata["provider"] == "DSTV"
    assert metadata["smart_card_number"] == "1234567890"
    assert metadata["package_code"] == "DTV-1"
    assert metadata["service_type"] == "COMPACT"
    assert metadata["provider_response"]["provider"] == "aidapay"
    assert metadata["attempted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("provider unavailable"), ProviderTemporaryFailure("provider temporary failure")],
)
async def test_tv_purchase_service_provider_failure_keeps_debit_pending_for_reconciliation(error: Exception) -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=error)

    ledger_service = FakeLedgerService()
    service = TVPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )

    with pytest.raises(PaymentException, match="outcome could not be confirmed") as exc_info:
        await service.purchase_tv(
            user_id=user_id,
            provider="GOTV",
            smart_card_number="1234567891",
            amount=Decimal("150.00"),
            transaction_pin="1234",
            package_code="GOTV-LITE",
            service_type="LITE",
        )

    assert provider_manager.calls[0]["operation"] == "subscribe_tv"
    assert wallet_repository.wallet.available_balance == Decimal("850.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("850.00")

    transaction = next(iter(transaction_repository.transactions.values()))
    assert transaction.status == "pending"
    metadata = json.loads(transaction.metadata_payload)
    assert metadata["provider_outcome_uncertain"] is True
    assert metadata["provider_error"] == "provider outcome could not be confirmed"
    assert str(error) not in metadata["provider_error"]
    assert str(error) not in str(exc_info.value)
    assert metadata["debit_applied"] is True
    assert len(ledger_service.entries) == 1


@pytest.mark.asyncio
async def test_tv_definitive_provider_failure_response_restores_wallet_debit() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(response={"status": "failed", "message": "declined"})
    ledger_service = FakeLedgerService()
    service = TVPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )

    result = await service.purchase_tv(
        user_id=user_id,
        provider="GOTV",
        smart_card_number="1234567891",
        amount=Decimal("150.00"),
        transaction_pin="1234",
        package_code="GOTV-LITE",
    )

    transaction = next(iter(transaction_repository.transactions.values()))
    assert result["status"] == "failed"
    assert wallet_repository.wallet.available_balance == Decimal("1000.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("1000.00")
    assert json.loads(transaction.metadata_payload)["reversal_applied"] is True
    assert [entry["entry_type"] for entry in ledger_service.entries] == ["DEBIT", "CREDIT"]


@pytest.mark.asyncio
async def test_tv_repeated_reconciliation_failure_reverses_wallet_once() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    wallet_repository.wallet.available_balance = Decimal("850.00")
    wallet_repository.wallet.ledger_balance = Decimal("850.00")
    transaction_repository = FakeTransactionRepository()
    ledger_service = FakeLedgerService()
    service = TVPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=FakeProviderManager(),
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
    )
    transaction = SimpleNamespace(
        id=uuid4(),
        reference="tv-reconcile-failure",
        user_id=user_id,
        wallet_id=wallet_repository.wallet.id,
        amount=Decimal("150.00"),
        currency="NGN",
        description="TV subscription",
        metadata_payload='{"debit_applied":true}',
    )

    await service.reverse_reconciled_failure(transaction=transaction, reason="confirmed failure")
    await service.reverse_reconciled_failure(transaction=transaction, reason="confirmed failure")

    assert wallet_repository.wallet.available_balance == Decimal("1000.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("1000.00")
    assert [entry["entry_type"] for entry in ledger_service.entries] == ["CREDIT"]
    assert json.loads(transaction.metadata_payload)["reversal_applied"] is True


@pytest.mark.asyncio
async def test_tv_uncertain_purchase_cannot_be_redispatched() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager(error=ProviderTemporaryFailure("provider temporary failure"))
    service = TVPurchaseService(
        wallet_service=FakeWalletService(wallet_repository),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
        ledger_service=FakeLedgerService(),
    )

    with pytest.raises(PaymentException):
        await service.purchase_tv(
            user_id=user_id,
            provider="GOTV",
            smart_card_number="1234567893",
            amount=Decimal("150.00"),
            transaction_pin="1234",
            package_code="GOTV-LITE",
        )
    transaction = next(iter(transaction_repository.transactions.values()))

    with pytest.raises(PaymentException, match="pending reconciliation"):
        await service.retry_tv_purchase(transaction=transaction)

    assert len(provider_manager.calls) == 1


def test_tv_purchase_service_does_not_import_concrete_vtu_providers() -> None:
    source = Path(r"c:\Users\HP\Downloads\CosmozPay\CosmozPay-Backend\app\services\tv\purchase.py").read_text(encoding="utf-8")

    assert "AidaPayProvider" not in source
    assert "VTUNG" not in source
    assert "VTUGate" not in source
    assert "ClubKonnect" not in source
