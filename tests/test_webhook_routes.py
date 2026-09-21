import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.controllers.webhook_controller import WebhookController
from app.integrations.payments.flutterwave.client import FlutterwaveClient
from app.routes.webhook_routes import get_webhook_service
from app.services.webhook_service import WebhookService
from app.utils.exceptions import ValidationException


class DummyWebhookService:
    def __init__(self, *, should_verify: bool = True) -> None:
        self.should_verify = should_verify
        self.verify_calls: list[dict[str, object]] = []
        self.processed_payloads: list[dict[str, object]] = []

    async def verify_signature(self, *, payload: bytes, signature: str | None, timestamp: str | None, secret: str | None) -> bool:
        self.verify_calls.append({"payload": payload, "signature": signature, "timestamp": timestamp, "secret": secret})
        return self.should_verify

    async def process_flutterwave_webhook(self, *, provider_name: str, event_id: str | None, payload: dict[str, object], signature: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, object]:
        self.processed_payloads.append({"provider_name": provider_name, "event_id": event_id, "payload": payload, "signature": signature, "timestamp": timestamp, "secret": secret})
        return {"status": "ok"}

    async def process_vtu_webhook(self, *, provider_name: str, payload: dict[str, object]) -> dict[str, object]:
        self.processed_payloads.append({"provider_name": provider_name, "payload": payload})
        return {"status": "ok"}

    async def process_notification_webhook(self, *, payload: dict[str, object]) -> dict[str, object]:
        self.processed_payloads.append({"payload": payload})
        return {"status": "ok"}


class DummyRequest:
    def __init__(self, payload: dict[str, object], *, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_flutterwave_client_uses_settings_for_base_url_and_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.payments.flutterwave.client.settings",
        SimpleNamespace(
            flutterwave_base_url="https://example.test/v3",
            flutterwave_secret_key=SecretStr("client-secret"),
            read_timeout=7,
            connection_timeout=3,
            write_timeout=9,
        ),
    )

    client = FlutterwaveClient()

    assert client.base_url == "https://example.test/v3"
    assert client.api_key == "client-secret"
    assert isinstance(client.timeout, httpx.Timeout)
    assert client.timeout.connect == 3.0
    assert client.timeout.read == 7.0
    assert client.timeout.write == 9.0


@pytest.mark.asyncio
async def test_get_webhook_service_builds_webhook_graph() -> None:
    service = await get_webhook_service(session=object())

    assert service is not None
    assert service.flutterwave_service is not None
    assert service.vtu_service is not None
    assert service.education_service is not None
    assert service.giftcard_service is not None
    assert service.notification_service is not None
    assert service.cloud_service is not None


@pytest.mark.asyncio
async def test_webhook_controller_uses_configured_secret_and_enforces_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyWebhookService(should_verify=True)
    controller = WebhookController(service)
    monkeypatch.setattr("app.controllers.webhook_controller.settings", SimpleNamespace(flutterwave_webhook_secret=SecretStr("configured-secret")))

    request = DummyRequest(
        {
            "provider_name": "flutterwave",
            "event_id": "evt-1",
            "payload": {"status": "paid"},
            "signature": "signature",
            "timestamp": "1234567890",
        },
        headers={"x-provider-name": "flutterwave", "x-webhook-signature": "signature", "x-webhook-timestamp": "1234567890"},
    )

    response = await controller.handle_payment_webhook(request)

    assert response["data"]["status"] == "ok"
    assert service.verify_calls[0]["secret"] == "configured-secret"
    assert service.processed_payloads[0]["secret"] == "configured-secret"


@pytest.mark.asyncio
async def test_webhook_controller_rejects_invalid_signature_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyWebhookService(should_verify=False)
    controller = WebhookController(service)
    monkeypatch.setattr("app.controllers.webhook_controller.settings", SimpleNamespace(flutterwave_webhook_secret=SecretStr("configured-secret")))

    request = DummyRequest(
        {
            "provider_name": "flutterwave",
            "event_id": "evt-2",
            "payload": {"status": "failed"},
            "signature": "bad-signature",
            "timestamp": "1234567890",
        },
        headers={"x-provider-name": "flutterwave", "x-webhook-signature": "bad-signature", "x-webhook-timestamp": "1234567890"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_payment_webhook(request)

    assert exc_info.value.status_code == 401
    assert not service.processed_payloads


@pytest.mark.asyncio
async def test_webhook_controller_rejects_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyWebhookService(should_verify=True)
    controller = WebhookController(service)
    monkeypatch.setattr("app.controllers.webhook_controller.settings", SimpleNamespace(flutterwave_webhook_secret=None))

    request = DummyRequest(
        {
            "provider_name": "flutterwave",
            "event_id": "evt-3",
            "payload": {"status": "pending"},
            "signature": "signature",
            "timestamp": "1234567890",
        },
        headers={"x-provider-name": "flutterwave", "x-webhook-signature": "signature", "x-webhook-timestamp": "1234567890"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_payment_webhook(request)

    assert exc_info.value.status_code == 401
    assert not service.processed_payloads


@pytest.mark.asyncio
async def test_provider_webhook_enforces_signature_and_handles_payload_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyWebhookService(should_verify=True)
    controller = WebhookController(service)
    monkeypatch.setattr("app.controllers.webhook_controller.settings", SimpleNamespace(flutterwave_webhook_secret=SecretStr("configured-secret")))

    request = DummyRequest(
        {
            "provider_name": "aidapay",
            "payload": {"event": "callback"},
            "signature": "signature",
            "timestamp": "1234567890",
        },
        headers={"x-provider-name": "aidapay", "x-webhook-signature": "signature", "x-webhook-timestamp": "1234567890"},
    )

    response = await controller.handle_provider_webhook(request)

    assert response["data"]["status"] == "ok"
    assert service.verify_calls
    assert service.processed_payloads[0]["provider_name"] == "aidapay"


@pytest.mark.asyncio
async def test_notification_webhook_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DummyWebhookService(should_verify=False)
    controller = WebhookController(service)
    monkeypatch.setattr("app.controllers.webhook_controller.settings", SimpleNamespace(flutterwave_webhook_secret=SecretStr("configured-secret")))

    request = DummyRequest(
        {
            "payload": {"event": "delivered"},
            "signature": "bad-signature",
            "timestamp": "1234567890",
        },
        headers={"x-webhook-signature": "bad-signature", "x-webhook-timestamp": "1234567890"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await controller.handle_notification_webhook(request)

    assert exc_info.value.status_code == 401
    assert not service.processed_payloads


@pytest.mark.asyncio
async def test_webhook_service_fails_when_notification_dependency_is_missing() -> None:
    service = WebhookService(notification_service=None)

    with pytest.raises(ValidationException):
        await service.process_notification_webhook(payload={"event": "delivered"})
