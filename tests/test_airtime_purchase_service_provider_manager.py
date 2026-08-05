from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.airtime.purchase import AirtimePurchaseService


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


class FakeWalletService:
    async def verify_transaction_pin(self, *, user_id, pin):
        return None


class FakeProviderManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        return {
            "provider": "aidapay",
            "data": {
                "success": True,
                "message": "processed",
                "data": {
                    "transaction_data": {
                        "status": "success",
                        "provider": "aidapay",
                        "transaction_hash": "prov-ref-001",
                        "transaction_id": "prov-tx-001",
                    }
                },
            },
        }


@pytest.mark.asyncio
async def test_airtime_purchase_service_executes_provider_manager_buy_airtime() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()

    service = AirtimePurchaseService(
        wallet_service=FakeWalletService(),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
    )

    result = await service.purchase_airtime(
        user_id=user_id,
        phone_number="08030000000",
        network="MTN",
        amount=Decimal("100.00"),
        transaction_pin="1234",
    )

    assert provider_manager.calls[0]["operation"] == "buy_airtime"
    assert provider_manager.calls[0]["network"] == "MTN"
    assert provider_manager.calls[0]["phone_number"] == "08030000000"
    assert result["status"] == "succeeded"
    assert result["provider"] == "aidapay"
    assert result["provider_reference"] == "prov-ref-001"
    assert result["provider_transaction_id"] == "prov-tx-001"
    assert wallet_repository.wallet.available_balance == Decimal("900.00")
    assert wallet_repository.wallet.ledger_balance == Decimal("900.00")


@pytest.mark.asyncio
async def test_airtime_purchase_service_processes_existing_transaction_with_provider_manager() -> None:
    user_id = uuid4()
    wallet_repository = FakeWalletRepository()
    wallet_repository.wallet.user_id = user_id
    transaction_repository = FakeTransactionRepository()
    provider_manager = FakeProviderManager()
    transaction = SimpleNamespace(
        reference="airtime-001",
        user_id=user_id,
        wallet_id=wallet_repository.wallet.id,
        transaction_type="airtime_purchase",
        category="airtime",
        amount=Decimal("50.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("50.00"),
        status="pending",
        provider_name=None,
        provider_reference=None,
        provider_transaction_id=None,
        external_reference=None,
        description="Airtime purchase",
        metadata_payload='{"phone_number":"08030000001","network":"GLO"}',
    )
    transaction_repository.transactions[transaction.reference] = transaction

    service = AirtimePurchaseService(
        wallet_service=FakeWalletService(),
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        transaction_repository=transaction_repository,
        user_repository=FakeUserRepository(),
        wallet_repository=wallet_repository,
    )

    result = await service.process_airtime_purchase(transaction=transaction)

    assert provider_manager.calls[0]["operation"] == "buy_airtime"
    assert provider_manager.calls[0]["network"] == "GLO"
    assert result["provider"] == "aidapay"
    assert transaction.provider_name == "aidapay"
    assert transaction.provider_reference == "prov-ref-001"