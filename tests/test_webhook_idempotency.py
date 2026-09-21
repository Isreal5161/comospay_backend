import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.services.payment.webhook import PaymentWebhookService
from app.services.webhook.idempotency import IdempotencyService


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, name, value, nx=False, ex=None):
        if nx:
            if name in self.store:
                return False
            self.store[name] = value
            return True
        self.store[name] = value
        return True

    async def get(self, name):
        return self.store.get(name)


class FakeSession:
    def __init__(self):
        self._lock = asyncio.Lock()

    def begin(self):
        return self

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


class FakeTransactionRepository:
    def __init__(self, transaction):
        self.transaction = transaction
        self.session = FakeSession()
        self.lock = asyncio.Lock()

    async def get_by_reference_for_update(self, reference):
        async with self.lock:
            return self.transaction

    async def update_transaction(self, transaction, **fields):
        for field, value in fields.items():
            setattr(transaction, field, value)
        return transaction


class FakeWalletRepository:
    def __init__(self, wallet):
        self.wallet = wallet
        self.lock = asyncio.Lock()

    async def get_by_id_for_update(self, wallet_id):
        async with self.lock:
            return self.wallet

    async def update_wallet(self, wallet, **fields):
        for field, value in fields.items():
            setattr(wallet, field, value)
        return wallet


def _signed_webhook(payload: dict, *, secret: str) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
    return signature, timestamp


@pytest.mark.asyncio
async def test_idempotency_first_then_duplicate(monkeypatch):
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr("app.services.webhook.idempotency.get_redis", _get_redis)

    svc = IdempotencyService()
    provider = "flutterwave"
    event_id = "evt-123"

    first = await svc.check_idempotency(event_id=event_id, provider_name=provider)
    assert first is True

    second = await svc.check_idempotency(event_id=event_id, provider_name=provider)
    assert second is False


@pytest.mark.asyncio
async def test_payment_webhook_rejects_duplicate_event_id() -> None:
    wallet = Wallet(
        id=uuid4(),
        user_id=uuid4(),
        wallet_reference="wallet-1",
        wallet_type="customer",
        currency="NGN",
        available_balance=Decimal("0"),
        ledger_balance=Decimal("0"),
        locked_balance=Decimal("0"),
        metadata_payload=None,
    )
    transaction = Transaction(
        id=uuid4(),
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        reference="ref-duplicate",
        transaction_type="payment",
        category="payment",
        amount=Decimal("100.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("100.00"),
        status="pending",
        provider_name="flutterwave",
        provider_reference="provider-ref",
        provider_transaction_id="provider-tx-1",
        external_reference=None,
        description="test payment",
        metadata_payload=None,
    )
    repo = FakeTransactionRepository(transaction)
    wallet_repo = FakeWalletRepository(wallet)
    service = PaymentWebhookService(
        transaction_repository=repo,
        wallet_repository=wallet_repo,
        provider_service=None,
    )

    payload = {
        "event": "charge.completed",
        "event_id": "evt-duplicate",
        "reference": "ref-duplicate",
        "status": "successful",
        "provider_reference": "provider-ref",
        "provider_transaction_id": "provider-tx-1",
    }
    signature, timestamp = _signed_webhook(payload, secret="secret")

    first = await service.process_payment_webhook(
        provider_name="flutterwave",
        event_id="evt-duplicate",
        payload=payload,
        signature=signature,
        timestamp=timestamp,
        secret="secret",
    )
    second = await service.process_payment_webhook(
        provider_name="flutterwave",
        event_id="evt-duplicate",
        payload=payload,
        signature=signature,
        timestamp=timestamp,
        secret="secret",
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert wallet.available_balance == Decimal("100.00")
    assert "evt-duplicate" in str(transaction.metadata_payload or "") or transaction.metadata_payload is not None


@pytest.mark.asyncio
async def test_payment_webhook_detects_duplicate_without_event_id() -> None:
    wallet = Wallet(
        id=uuid4(),
        user_id=uuid4(),
        wallet_reference="wallet-2",
        wallet_type="customer",
        currency="NGN",
        available_balance=Decimal("0"),
        ledger_balance=Decimal("0"),
        locked_balance=Decimal("0"),
        metadata_payload=None,
    )
    transaction = Transaction(
        id=uuid4(),
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        reference="ref-no-event",
        transaction_type="payment",
        category="payment",
        amount=Decimal("250.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("250.00"),
        status="pending",
        provider_name="flutterwave",
        provider_reference="provider-ref-2",
        provider_transaction_id="provider-tx-2",
        external_reference=None,
        description="repeat payment",
        metadata_payload=None,
    )
    repo = FakeTransactionRepository(transaction)
    wallet_repo = FakeWalletRepository(wallet)
    service = PaymentWebhookService(
        transaction_repository=repo,
        wallet_repository=wallet_repo,
        provider_service=None,
    )

    payload = {
        "event": "charge.completed",
        "reference": "ref-no-event",
        "status": "successful",
        "provider_reference": "provider-ref-2",
        "provider_transaction_id": "provider-tx-2",
    }
    signature, timestamp = _signed_webhook(payload, secret="secret")

    first = await service.process_payment_webhook(
        provider_name="flutterwave",
        event_id=None,
        payload=payload,
        signature=signature,
        timestamp=timestamp,
        secret="secret",
    )
    second = await service.process_payment_webhook(
        provider_name="flutterwave",
        event_id=None,
        payload=payload,
        signature=signature,
        timestamp=timestamp,
        secret="secret",
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert wallet.available_balance == Decimal("250.00")


@pytest.mark.asyncio
async def test_payment_webhook_concurrent_duplicate_events_only_one_wallet_credit() -> None:
    wallet = Wallet(
        id=uuid4(),
        user_id=uuid4(),
        wallet_reference="wallet-3",
        wallet_type="customer",
        currency="NGN",
        available_balance=Decimal("0"),
        ledger_balance=Decimal("0"),
        locked_balance=Decimal("0"),
        metadata_payload=None,
    )
    transaction = Transaction(
        id=uuid4(),
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        reference="ref-concurrent",
        transaction_type="payment",
        category="payment",
        amount=Decimal("300.00"),
        currency="NGN",
        charges=Decimal("0"),
        total_amount=Decimal("300.00"),
        status="pending",
        provider_name="flutterwave",
        provider_reference="provider-ref-3",
        provider_transaction_id="provider-tx-3",
        external_reference=None,
        description="concurrent payment",
        metadata_payload=None,
    )
    repo = FakeTransactionRepository(transaction)
    wallet_repo = FakeWalletRepository(wallet)
    service = PaymentWebhookService(
        transaction_repository=repo,
        wallet_repository=wallet_repo,
        provider_service=None,
    )

    payload = {
        "event": "charge.completed",
        "event_id": "evt-concurrent",
        "reference": "ref-concurrent",
        "status": "successful",
        "provider_reference": "provider-ref-3",
        "provider_transaction_id": "provider-tx-3",
    }
    signature, timestamp = _signed_webhook(payload, secret="secret")

    async def invoke_once() -> dict:
        return await service.process_payment_webhook(
            provider_name="flutterwave",
            event_id="evt-concurrent",
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret="secret",
        )

    first, second = await asyncio.gather(invoke_once(), invoke_once())

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert wallet.available_balance == Decimal("300.00")
