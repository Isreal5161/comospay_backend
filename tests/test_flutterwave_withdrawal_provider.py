from __future__ import annotations

from decimal import Decimal

import pytest

from app.integrations.payments.flutterwave.authentication import (
    FlutterwaveAuthentication,
)
from app.integrations.payments.flutterwave.client import (
    FlutterwaveAPIError,
    FlutterwaveRequestError,
)
from app.integrations.payments.flutterwave.exceptions import (
    FlutterwaveAPIError as FlutterwaveIntegrationAPIError,
    FlutterwaveTimeoutError,
)
from app.integrations.payments.flutterwave.transfers import FlutterwaveTransferService
from app.integrations.payments.flutterwave.withdrawal import FlutterwaveWithdrawalProvider
from app.models.provider import Provider
from app.services.provider_service import ProviderService


class FakeClient:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def post(self, path, **kwargs):
        self.calls.append({"path": path, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def provider(response=None, error: Exception | None = None):
    client = FakeClient(response=response, error=error)
    transfer_service = FlutterwaveTransferService(
        client=client,
        authentication=FlutterwaveAuthentication("test-secret"),
    )
    return FlutterwaveWithdrawalProvider(transfer_service), client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "expected_status"),
    [("successful", "success"), ("pending", "pending"), ("failed", "failed")],
)
async def test_transfer_normalizes_flutterwave_status(provider_status, expected_status) -> None:
    adapter, client = provider(
        response={
            "status": "success",
            "data": {
                "status": provider_status,
                "reference": "FW-REF",
                "id": 123,
                "amount": 1000,
                "currency": "NGN",
            },
        }
    )

    result = await adapter.transfer(
        account_details={"bank_code": "044", "account_number": "0123456789", "currency": "NGN"},
        amount=Decimal("1000.00"),
        reference="wdl-stable-ref",
    )

    assert result["status"] == expected_status
    assert result["provider_reference"] == "FW-REF"
    assert result["provider_transaction_id"] == "123"
    assert client.calls[0]["path"] == "/transfers"


@pytest.mark.asyncio
async def test_transfer_propagates_reference_to_payload_and_idempotency_key() -> None:
    adapter, client = provider(response={"status": "success", "data": {"status": "pending"}})

    await adapter.transfer(
        account_details={"bank_code": "044", "account_number": "0123456789"},
        amount=1000,
        reference="wdl-stable-ref",
    )

    request = client.calls[0]
    assert request["json"]["reference"] == "wdl-stable-ref"
    assert request["headers"]["Idempotency-Key"] == "transfer-wdl-stable-ref"
    assert request["headers"]["Authorization"] == "Bearer test-secret"


@pytest.mark.asyncio
async def test_timeout_is_normalized() -> None:
    adapter, _ = provider(error=FlutterwaveRequestError("Flutterwave request timed out"))

    with pytest.raises(FlutterwaveTimeoutError):
        await adapter.transfer(
            account_details={"bank_code": "044", "account_number": "0123456789"},
            amount=1000,
            reference="wdl-timeout",
        )


@pytest.mark.asyncio
async def test_http_error_is_normalized_without_raw_provider_body() -> None:
    adapter, _ = provider(error=FlutterwaveAPIError("Flutterwave API error 502"))

    with pytest.raises(FlutterwaveIntegrationAPIError) as exc_info:
        await adapter.transfer(
            account_details={"bank_code": "044", "account_number": "0123456789"},
            amount=1000,
            reference="wdl-http-error",
        )

    assert "secret" not in str(exc_info.value).lower()
    assert "authorization" not in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_malformed_response_is_normalized() -> None:
    adapter, _ = provider(response={"status": "success", "data": []})

    with pytest.raises(FlutterwaveIntegrationAPIError, match="malformed"):
        await adapter.transfer(
            account_details={"bank_code": "044", "account_number": "0123456789"},
            amount=1000,
            reference="wdl-malformed",
        )


def test_missing_authentication_configuration_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.payments.flutterwave.authentication.settings.flutterwave_secret_key",
        None,
    )
    with pytest.raises(Exception, match="secret key is not configured"):
        FlutterwaveAuthentication().get_headers()


@pytest.mark.asyncio
async def test_provider_service_resolves_flutterwave_transfer_adapter() -> None:
    adapter, _ = provider(response={"status": "success", "data": {"status": "pending"}})
    provider_record = Provider(name="Flutterwave", code="flutterwave", category="Payments")

    class Selector:
        async def select_provider(self, **kwargs):
            return provider_record

    class Failover:
        async def execute_with_failover(self, *, operation, **kwargs):
            return await operation(provider_record)

    class Repository:
        async def get_provider_by_id(self, provider_id):
            return provider_record

    service = ProviderService(
        selector=Selector(),
        health_service=object(),
        failover_service=Failover(),
        provider_repository=Repository(),
    )
    result = await service.execute_transfer(
        operation=lambda _: adapter.transfer(
            account_details={"bank_code": "044", "account_number": "0123456789"},
            amount=1000,
            reference="wdl-resolved",
        )
    )

    assert result["provider"]["code"] == "flutterwave"
    assert result["result"]["data"]["status"] == "pending"