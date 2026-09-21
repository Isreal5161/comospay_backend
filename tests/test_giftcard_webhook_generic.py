from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.controllers.webhook_controller import WebhookController
from app.services.webhook.giftcard import GiftCardWebhookService
from app.services.webhook_service import WebhookService
from app.utils.exceptions import ValidationException


class ProviderRepositoryFake:
    def __init__(self, providers):
        self.providers = providers

    async def get_active_providers(self, **kwargs):
        assert kwargs == {"category": "Gift Cards"}
        return [provider for provider in self.providers if provider.is_active]


class GiftCardServiceFake:
    def __init__(self):
        self.calls = []
        self.provider_operations = []

    def build_provider_operation(self, **kwargs):
        operation = {"operation": kwargs.get("operation"), "payload": kwargs.get("payload"), "provider_name": kwargs.get("provider_name")}
        self.provider_operations.append(operation)
        return object()

    async def reconcile_transaction(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "succeeded", **kwargs}


class IdempotencyFake:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []
        self.releases = []

    async def check_idempotency(self, **kwargs):
        self.calls.append(kwargs)
        return self.accepted

    async def release_lock(self, **kwargs):
        self.releases.append(kwargs)


def provider(*, code="sogo", is_active=True, status="active"):
    return SimpleNamespace(
        id=uuid4(),
        code=code,
        name=f"{code} gift cards",
        category="Gift Cards",
        is_active=is_active,
        status=status,
    )


@pytest.mark.asyncio
async def test_generic_giftcard_webhook_resolves_active_provider_and_reconciles():
    giftcard = GiftCardServiceFake()
    service = GiftCardWebhookService(
        giftcard_service=giftcard,
        provider_repository=ProviderRepositoryFake([provider(code="sogo")]),
    )

    result = await service.process_webhook(
        provider_name="SOGO",
        payload={"transaction_reference": "giftcard-buy-1", "status": "completed"},
    )

    assert result["status"] == "succeeded"
    assert giftcard.calls == [{"reference": "giftcard-buy-1", "provider_name": "sogo"}]


@pytest.mark.asyncio
async def test_generic_giftcard_webhook_rejects_unknown_or_inactive_provider():
    service = GiftCardWebhookService(
        giftcard_service=GiftCardServiceFake(),
        provider_repository=ProviderRepositoryFake([provider(code="disabled", is_active=False)]),
    )

    with pytest.raises(ValidationException, match="unknown or inactive"):
        await service.process_webhook(provider_name="sogo", payload={"reference": "ref-1"})


@pytest.mark.asyncio
async def test_generic_giftcard_webhook_resolves_provider_reference_when_reference_missing():
    giftcard = GiftCardServiceFake()
    service = GiftCardWebhookService(
        giftcard_service=giftcard,
        provider_repository=ProviderRepositoryFake([provider(code="sogo")]),
    )

    class LookupRepository:
        async def get_by_provider_reference(self, provider_reference, *, provider_name=None):
            assert provider_reference == "SOGO-REF-42"
            assert provider_name == "sogo"
            return SimpleNamespace(reference="giftcard-buy-provider-ref")

    service.giftcard_service.reconciliation_service = SimpleNamespace(transaction_repository=LookupRepository())

    await service.process_webhook(
        provider_name="SOGO",
        payload={"provider_reference": "SOGO-REF-42", "status": "completed"},
    )

    assert giftcard.calls[0]["reference"] == "giftcard-buy-provider-ref"
    assert giftcard.calls[0]["provider_name"] == "sogo"


@pytest.mark.asyncio
async def test_generic_giftcard_webhook_rejects_missing_reference_before_reconciliation():
    giftcard = GiftCardServiceFake()
    service = GiftCardWebhookService(
        giftcard_service=giftcard,
        provider_repository=ProviderRepositoryFake([provider()]),
    )

    with pytest.raises(ValidationException, match="transaction reference is required"):
        await service.process_webhook(provider_name="sogo", payload={"status": "completed"})

    assert not giftcard.calls


@pytest.mark.asyncio
async def test_webhook_service_deduplicates_giftcard_events():
    giftcard = SimpleNamespace(
        process_webhook=lambda **kwargs: None,
    )
    idempotency = IdempotencyFake(accepted=False)
    service = WebhookService(giftcard_service=giftcard, idempotency_service=idempotency)

    result = await service.process_giftcard_webhook(
        provider_name="sogo",
        event_id="evt-1",
        payload={"reference": "ref-1"},
    )

    assert result == {"status": "duplicate", "provider_name": "sogo"}
    assert idempotency.calls == [{"event_id": "evt-1", "provider_name": "sogo"}]


@pytest.mark.asyncio
async def test_webhook_service_releases_idempotency_after_success():
    idempotency = IdempotencyFake(accepted=True)
    calls = []

    class GiftCardWebhookFake:
        async def process_webhook(self, **kwargs):
            calls.append(kwargs)
            return {"status": "succeeded"}

    service = WebhookService(
        giftcard_service=GiftCardWebhookFake(),
        idempotency_service=idempotency,
    )

    result = await service.process_giftcard_webhook(
        provider_name="sogo",
        event_id="evt-2",
        payload={"reference": "ref-2"},
    )

    assert result["status"] == "succeeded"
    assert calls == [{"provider_name": "sogo", "payload": {"reference": "ref-2"}}]
    assert idempotency.releases == [{"event_id": "evt-2", "provider_name": "sogo"}]


class RequestFake:
    def __init__(self, payload, headers):
        self.payload = payload
        self.headers = headers

    async def body(self):
        return json.dumps(self.payload).encode()


class ControllerServiceFake:
    def __init__(self, verified):
        self.verified = verified
        self.processed = False

    async def verify_signature(self, **kwargs):
        return self.verified

    async def process_giftcard_webhook(self, **kwargs):
        self.processed = True
        return {"status": "succeeded"}


@pytest.mark.asyncio
async def test_sogo_giftcard_controller_accepts_valid_payload_and_signature(monkeypatch):
    service = ControllerServiceFake(verified=True)
    controller = WebhookController(service)
    monkeypatch.setattr(
        "app.controllers.webhook_controller.settings",
        SimpleNamespace(sogo_webhook_secret="configured-secret"),
    )
    payload = {
        "event_type": "transaction_completed",
        "reference": "giftcard-buy-1",
        "provider_reference": "SOGO-XYZ789",
        "status": "success",
    }
    body = json.dumps(payload).encode()
    signature = "hmac-sha256=" + hmac.new(b"configured-secret", body, hashlib.sha256).hexdigest()
    request = RequestFake(payload, {"x-sogo-signature": signature})

    response = await controller.handle_sogo_giftcard_webhook(request)

    assert response["data"]["status"] == "succeeded"
    assert service.processed is True


@pytest.mark.asyncio
async def test_sogo_giftcard_controller_rejects_missing_required_fields(monkeypatch):
    service = ControllerServiceFake(verified=True)
    controller = WebhookController(service)
    monkeypatch.setattr(
        "app.controllers.webhook_controller.settings",
        SimpleNamespace(sogo_webhook_secret="configured-secret"),
    )
    request = RequestFake({"event_type": "transaction_completed", "reference": "giftcard-buy-1"}, {"x-sogo-signature": "hmac-sha256=abc"})

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_sogo_giftcard_webhook(request)

    assert exc_info.value.status_code == 400
    assert not service.processed


@pytest.mark.asyncio
async def test_sogo_giftcard_controller_rejects_invalid_signature(monkeypatch):
    service = ControllerServiceFake(verified=True)
    controller = WebhookController(service)
    monkeypatch.setattr(
        "app.controllers.webhook_controller.settings",
        SimpleNamespace(sogo_webhook_secret="configured-secret"),
    )
    payload = {
        "event_type": "transaction_completed",
        "reference": "giftcard-buy-1",
        "provider_reference": "SOGO-XYZ789",
        "status": "success",
    }
    request = RequestFake(payload, {"x-sogo-signature": "hmac-sha256=bad"})

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_sogo_giftcard_webhook(request)

    assert exc_info.value.status_code == 401
    assert not service.processed


@pytest.mark.asyncio
async def test_giftcard_controller_verifies_signature_before_processing(monkeypatch):
    service = ControllerServiceFake(verified=False)
    controller = WebhookController(service)
    monkeypatch.setattr(
        "app.controllers.webhook_controller.settings",
        SimpleNamespace(sogo_webhook_secret="configured-secret"),
    )
    request = RequestFake(
        {"provider_name": "sogo", "reference": "ref-3"},
        {"x-webhook-signature": "bad", "x-webhook-timestamp": "1234567890"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_giftcard_webhook(request)

    assert exc_info.value.status_code == 401
    assert not service.processed
