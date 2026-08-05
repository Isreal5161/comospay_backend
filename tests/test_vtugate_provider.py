from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError
from app.integrations.airtime.vtugate import VTUGateProvider


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, *, json_error: bool = False) -> None:
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


class _FakeAsyncClient:
    queue: list[Any] = []
    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def post(self, *, url: str, headers: dict[str, str], data: dict[str, str]) -> Any:
        _FakeAsyncClient.calls.append({"url": url, "headers": headers, "data": data})
        if not _FakeAsyncClient.queue:
            raise AssertionError("No queued fake response for request")
        item = _FakeAsyncClient.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def patch_async_client(monkeypatch: pytest.MonkeyPatch):
    _FakeAsyncClient.queue = []
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    yield _FakeAsyncClient


@pytest.fixture
def provider() -> VTUGateProvider:
    return VTUGateProvider(api_key="token", base_url="https://api.vtugate.com")


def test_provider_available_with_credentials() -> None:
    vtugate = VTUGateProvider(api_key="token", base_url="https://api.vtugate.com")
    assert vtugate.is_available is True


def test_provider_unavailable_without_credentials() -> None:
    vtugate = VTUGateProvider(api_key=None, base_url="")
    assert vtugate.is_available is False


@pytest.mark.asyncio
async def test_fetch_account_details_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "ok", "data": {"wallet_balance": 5000}}),
    ]

    result = await provider.fetch_account_details()

    assert result["status"] is True
    assert patch_async_client.calls[0]["url"].endswith("/api/v1/accountdetails")


@pytest.mark.asyncio
async def test_fetch_balance_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "ok", "data": {"wallet_balance": 5240.5}}),
    ]

    result = await provider.fetch_balance()

    assert result["status"] is True
    assert result["data"]["wallet_balance"] == 5240.5


@pytest.mark.asyncio
async def test_verify_transaction_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "ok", "data": {"external_reference": "ABC123XYZ"}}),
    ]

    result = await provider.verify_transaction(provider_reference="ABC123XYZ")

    assert result["status"] is True
    assert patch_async_client.calls[0]["data"]["external_reference"] == "ABC123XYZ"


@pytest.mark.asyncio
async def test_fetch_services_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "service_type": "airtime", "data": [{"service_id": 136, "network_name": "mtn"}]}),
    ]

    result = await provider.fetch_services(service_type="airtime")

    assert result["status"] is True
    assert patch_async_client.calls[0]["url"].endswith("/api/v1/fetchservices")


@pytest.mark.asyncio
async def test_buy_airtime_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 136, "network_name": "mtn"}]}),
        _FakeResponse(200, {"status": True, "message": "Airtime purchase was successful", "data": {"transaction_id": 45231}}),
    ]

    result = await provider.buy_airtime(phone_number="08012345678", network="mtn", amount=1000)

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/buyairtime")
    assert patch_async_client.calls[-1]["data"]["service_id"] == "136"


@pytest.mark.asyncio
async def test_fetch_data_plans_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 201, "network_name": "mtn"}]}),
        _FakeResponse(200, {"status": True, "data": [{"plan_code": "1", "amount": 350}]}),
    ]

    result = await provider.fetch_data_plans(network="mtn")

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/fetchdataplans")


@pytest.mark.asyncio
async def test_buy_data_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 201, "network_name": "mtn"}]}),
        _FakeResponse(200, {"status": True, "data": [{"plan_code": "1", "amount": 350, "plan_name": "1GB"}]}),
        _FakeResponse(200, {"status": True, "message": "Data purchase was successful", "data": {"transaction_id": 45232}}),
    ]

    result = await provider.buy_data(phone_number="08012345678", network="mtn", bundle_code="1")

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/buydata")
    assert patch_async_client.calls[-1]["data"]["plan_code"] == "1"
    assert patch_async_client.calls[-1]["data"]["amount"] == "350"


@pytest.mark.asyncio
async def test_verify_electricity_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 301, "network_name": "ibedc"}]}),
        _FakeResponse(200, {"status": True, "message": "Verification successful", "data": {"customer_name": "Ada"}}),
    ]

    result = await provider.verify_electricity(meter_number="234567890567", provider="ibedc")

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/verifyelectricity")


@pytest.mark.asyncio
async def test_purchase_electricity_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 301, "network_name": "ibedc"}]}),
        _FakeResponse(200, {"status": True, "message": "Purchase successful", "data": {"transaction_id": 45233}}),
    ]

    result = await provider.purchase_electricity(
        meter_number="234567890567",
        provider="ibedc",
        amount=1000,
        phone_number="08012345678",
    )

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/buyelectricity")


@pytest.mark.asyncio
async def test_fetch_electricity_providers_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "service_type": "electricity", "data": [{"service_id": 301, "network_name": "ibedc"}]}),
    ]

    result = await provider.fetch_electricity_providers()

    assert result["status"] is True
    assert patch_async_client.calls[-1]["data"]["service_type"] == "electricity"


@pytest.mark.asyncio
async def test_verify_cable_tv_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 401, "network_name": "dstv"}]}),
        _FakeResponse(200, {"status": True, "message": "Verification successful", "data": {"customer_name": "Ada"}}),
    ]

    result = await provider.verify_cable_tv(
        smart_card_number="1234567890",
        provider_code="dstv",
        phone="08012345678",
    )

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/verifycabletv")


@pytest.mark.asyncio
async def test_subscribe_tv_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "data": [{"service_id": 401, "network_name": "dstv"}]}),
        _FakeResponse(200, {"status": True, "message": "Purchase successful", "data": {"transaction_id": 45234}}),
    ]

    result = await provider.subscribe_tv(
        smart_card_number="1234567890",
        provider_code="dstv",
        package="Yanga",
        package_code="yanga",
        amount=2950,
        phone="08012345678",
    )

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/buycabletv")


@pytest.mark.asyncio
async def test_fetch_cable_tv_providers_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "service_type": "tv", "data": [{"service_id": 401, "network_name": "dstv"}]}),
    ]

    result = await provider.fetch_cable_tv_providers()

    assert result["status"] is True
    assert patch_async_client.calls[-1]["data"]["service_type"] == "tv"


@pytest.mark.asyncio
async def test_get_education_price_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "Price fetched", "data": [{"product_code": "waec", "price": 3500}]}),
    ]

    result = await provider.get_education_price(service_id=1)

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/geteducationtypeprice")


@pytest.mark.asyncio
async def test_buy_education_pins_success(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "Education pin purchase was successful", "data": {"pins": ["1234567890"]}}),
    ]

    result = await provider.buy_education_pins(service_id=1, phone="08012345678", quantity=1, product_code="waec")

    assert result["status"] is True
    assert patch_async_client.calls[-1]["url"].endswith("/api/v1/buyeducation")


@pytest.mark.asyncio
async def test_timeout_maps_to_temporary_failure(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [httpx.TimeoutException("timed out")]

    with pytest.raises(ProviderTemporaryFailure):
        await provider.fetch_account_details()


@pytest.mark.asyncio
async def test_network_error_maps_to_temporary_failure(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [httpx.RequestError("connection dropped")]

    with pytest.raises(ProviderTemporaryFailure):
        await provider.fetch_account_details()


@pytest.mark.asyncio
async def test_authentication_failure_maps_to_unavailable(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [_FakeResponse(401, {"status": False, "message": "Invalid API key."})]

    with pytest.raises(ProviderUnavailableError):
        await provider.fetch_account_details()


@pytest.mark.asyncio
async def test_business_failure_maps_to_unavailable(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [_FakeResponse(200, {"status": False, "message": "Insufficient wallet balance!"})]

    with pytest.raises(ProviderUnavailableError):
        await provider.buy_airtime(phone_number="08012345678", network="136", amount=1000)


@pytest.mark.asyncio
async def test_upstream_5xx_maps_to_temporary_failure(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [_FakeResponse(503, {"status": False, "message": "Service temporarily unavailable"})]

    with pytest.raises(ProviderTemporaryFailure):
        await provider.fetch_account_details()


@pytest.mark.asyncio
async def test_missing_credentials_raise_unavailable_on_request(patch_async_client: _FakeAsyncClient) -> None:
    vtugate = VTUGateProvider(api_key=None, base_url="")

    with pytest.raises(ProviderUnavailableError):
        await vtugate.fetch_account_details()


@pytest.mark.asyncio
async def test_provider_returns_raw_dictionary_for_manager_compatibility(provider: VTUGateProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"status": True, "message": "ok", "data": {"wallet_balance": 5000}}),
    ]

    result = await provider.fetch_account_details()

    assert isinstance(result, dict)
    assert "provider" not in result
    assert "data" in result
