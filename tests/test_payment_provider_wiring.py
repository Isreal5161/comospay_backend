from __future__ import annotations

import contextlib
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.controllers.payment_controller import PaymentController, PaymentInitializeRequest
from app.integrations.payments.flutterwave.payments import FlutterwavePaymentService
from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.payment.payment import PaymentManager
from app.services.payment_service import PaymentService


class FakePaymentManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def initialize_payment(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("provider_operation") is None:
            raise AssertionError("provider_operation callback was not supplied")
        provider = Provider(name="flutterwave", code="flutterwave", category="Payments")
        await kwargs["provider_operation"](provider)
        return {"reference": kwargs["reference"], "status": "pending"}

    async def verify_payment(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("provider_operation") is None:
            raise AssertionError("provider_operation callback was not supplied")
        provider = Provider(name="flutterwave", code="flutterwave", category="Payments")
        await kwargs["provider_operation"](provider)
        return {"reference": kwargs["reference"], "status": "pending"}


class FakeIntegration:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def initialize_payment(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "pending", "provider_reference": "flutterwave-ref"}

    async def verify_transaction(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "succeeded", "provider_reference": "flutterwave-ref"}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        return {
            "status": "success",
            "message": "initialized",
            "data": {
                "link": "https://checkout.example/pay",
                "tx_ref": "pay-001",
                "id": 12345,
            },
        }


class FakeUserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id)


class FakeWalletRepository:
    def __init__(self) -> None:
        self.wallet = SimpleNamespace(id=uuid4(), user_id=uuid4(), is_active=True, is_suspended=False, is_frozen=False)

    async def get_user_wallet(self, *, user_id):
        return self.wallet

    async def update_wallet(self, wallet, **kwargs):
        return wallet


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.transactions: dict[str, Transaction] = {}
        self.calls: list[dict] = []
        self.session = SimpleNamespace(begin=self._begin)

    def _begin(self):
        @contextlib.asynccontextmanager
        async def _ctx():
            yield

        return _ctx()

    async def get_by_reference(self, reference):
        return self.transactions.get(reference)

    async def create_transaction(self, transaction):
        self.transactions[transaction.reference] = transaction
        return transaction

    async def update_transaction(self, transaction, **kwargs):
        self.calls.append({"reference": transaction.reference, **kwargs})
        return transaction


class FakeProviderService:
    def __init__(self) -> None:
        self.calls = []

    async def execute_payment(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "pending",
            "provider": "flutterwave",
            "provider_reference": "flutterwave-ref",
            "provider_transaction_id": "fw-123",
            "payment_link": "https://checkout.example/pay",
            "checkout_url": "https://checkout.example/pay",
            "authorization_url": "https://checkout.example/authorize",
            "message": "initialized",
            "metadata": {"test": True},
        }


@pytest.mark.asyncio
async def test_controller_initialization_provides_provider_callback_to_payment_manager() -> None:
    fake_manager = FakePaymentManager()
    payment_service = PaymentService(payment_manager=fake_manager, webhook_service=SimpleNamespace())
    payment_service._build_provider_integration = lambda **kwargs: FakeIntegration()

    controller = PaymentController(payment_service=payment_service)
    payload = PaymentInitializeRequest(
        amount=Decimal("25.50"),
        reference="pay-001",
        currency="NGN",
        description="Test payment",
    )

    result = await controller.initialize_payment(payload, user_id=uuid4())

    assert result["data"]["reference"] == "pay-001"
    assert fake_manager.calls[0]["provider_operation"] is not None


@pytest.mark.asyncio
async def test_payment_service_provider_callback_reaches_provider_specific_integration() -> None:
    fake_manager = FakePaymentManager()
    payment_service = PaymentService(payment_manager=fake_manager, webhook_service=SimpleNamespace())
    fake_integration = FakeIntegration()
    payment_service._build_provider_integration = lambda **kwargs: fake_integration

    await payment_service.initialize_payment(
        user_id=uuid4(),
        amount=Decimal("10.00"),
        reference="pay-002",
        currency="NGN",
        description="Callback test",
    )

    assert fake_integration.calls
    assert fake_integration.calls[0]["tx_ref"] == "pay-002"


@pytest.mark.asyncio
async def test_payment_service_provider_callback_supports_verification_flow() -> None:
    fake_manager = FakePaymentManager()
    payment_service = PaymentService(payment_manager=fake_manager, webhook_service=SimpleNamespace())
    fake_integration = FakeIntegration()
    payment_service._build_provider_integration = lambda **kwargs: fake_integration

    await payment_service.verify_payment(reference="pay-003")

    assert fake_integration.calls
    assert fake_integration.calls[0]["tx_ref"] == "pay-003"


@pytest.mark.asyncio
async def test_flutterwave_payment_service_uses_reference_for_idempotency_key() -> None:
    client = FakeClient()
    service = FlutterwavePaymentService(client=client)

    await service.initialize_payment(tx_ref="pay-idempotency", amount=10, currency="NGN")
    first_headers = client.calls[0]["headers"]
    await service.initialize_payment(tx_ref="pay-idempotency", amount=10, currency="NGN")
    second_headers = client.calls[1]["headers"]

    assert first_headers["Idempotency-Key"] == second_headers["Idempotency-Key"]


@pytest.mark.asyncio
async def test_flutterwave_payment_service_forwards_redirect_url_to_provider_payload() -> None:
    client = FakeClient()
    service = FlutterwavePaymentService(client=client)

    await service.initialize_payment(
        tx_ref="pay-redirect",
        amount=10,
        currency="NGN",
        redirect_url="https://example.com/complete",
    )

    request_payload = client.calls[0]["json"]
    assert request_payload["redirect_url"] == "https://example.com/complete"


def test_payment_manager_normalization_preserves_provider_urls() -> None:
    manager = PaymentManager(
        user_repository=FakeUserRepository(),
        wallet_repository=FakeWalletRepository(),
        transaction_repository=FakeTransactionRepository(),
        provider_service=FakeProviderService(),
    )

    normalized = manager._normalize_provider_response(
        {
            "status": "pending",
            "message": "initialized",
            "data": {"link": "https://checkout.example/pay"},
        },
        Provider(name="flutterwave", code="flutterwave", category="Payments"),
    )

    assert normalized["payment_link"] == "https://checkout.example/pay"
    assert normalized["checkout_url"] == "https://checkout.example/pay"
    assert normalized["authorization_url"] is None


@pytest.mark.asyncio
async def test_payment_manager_does_not_recreate_duplicate_transaction_for_same_reference() -> None:
    transaction_repo = FakeTransactionRepository()
    provider_service = FakeProviderService()
    manager = PaymentManager(
        user_repository=FakeUserRepository(),
        wallet_repository=FakeWalletRepository(),
        transaction_repository=transaction_repo,
        provider_service=provider_service,
    )

    first = await manager.initialize_payment(
        user_id=uuid4(),
        amount=Decimal("10.00"),
        reference="dup-ref",
        currency="NGN",
        description="dup-test",
        provider_operation=lambda provider: None,
    )
    second = await manager.initialize_payment(
        user_id=first["wallet_id"] and uuid4() or uuid4(),
        amount=Decimal("10.00"),
        reference="dup-ref",
        currency="NGN",
        description="dup-test",
        provider_operation=lambda provider: None,
    )

    assert first["reference"] == second["reference"]
    assert len(provider_service.calls) == 1
