from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.airtime.aidapay import AidaPayProvider
from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError


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

    async def request(self, *, method: str, url: str, headers: dict[str, str], json: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": method, "url": url, "headers": headers, "json": json})
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
def provider() -> AidaPayProvider:
    return AidaPayProvider(api_key="token", base_url="https://www.aidapay.ng/api/v1", account_pin="1234")


@pytest.mark.asyncio
async def test_health_check_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"success": True, "message": "success", "data": {"balance": 15000}})
    ]

    result = await provider.health_check()

    assert result["success"] is True
    assert result["message"] == "success"
    assert result["data"]["balance"] == 15000


@pytest.mark.asyncio
async def test_check_balance_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"success": True, "message": "success", "data": {"balance": 15000}})
    ]

    result = await provider.check_balance()

    assert result["success"] is True
    assert result["data"]["balance"] == 15000


@pytest.mark.asyncio
async def test_buy_airtime_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(
            200,
            {
                "success": True,
                "data": [
                    {"provider_name": "MTN", "provider_code": "mtn-airtime"},
                    {"provider_name": "Airtel", "provider_code": "airtel-airtime"},
                ],
            },
        ),
        _FakeResponse(
            200,
            {
                "success": True,
                "message": "success",
                "data": {"success": True, "transaction_data": {"transaction_hash": "TX001"}},
            },
        ),
    ]

    result = await provider.buy_airtime(
        phone_number="08012345678",
        network="MTN",
        amount=100,
        reference="REF-12345",
    )

    assert result["success"] is True
    buy_call = patch_async_client.calls[-1]
    assert buy_call["json"]["provider_code"] == "mtn-airtime"
    assert buy_call["json"]["account_pin"] == "1234"


@pytest.mark.asyncio
async def test_buy_data_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"success": True, "data": [{"provider_name": "MTN", "provider_code": "mtn-data"}]}),
        _FakeResponse(
            200,
            {
                "success": True,
                "message": "success",
                "data": {"success": True, "transaction_data": {"transaction_hash": "TX002"}},
            },
        ),
    ]

    result = await provider.buy_data(
        phone_number="08012345678",
        network="MTN",
        bundle_code="DATA-01",
        reference="REF-22345",
    )

    assert result["success"] is True
    buy_call = patch_async_client.calls[-1]
    assert buy_call["json"]["package_code"] == "DATA-01"


@pytest.mark.asyncio
async def test_verify_transaction_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(
            200,
            {
                "success": True,
                "message": "success",
                "data": {"transaction_hash": "TX003", "status": "Completed"},
            },
        )
    ]

    result = await provider.verify_transaction(provider_reference="TX003")

    assert result["success"] is True
    assert result["data"]["status"] == "Completed"


@pytest.mark.asyncio
async def test_fetch_supported_networks_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"success": True, "data": [{"provider_name": "MTN", "provider_code": "mtn-airtime"}]})
    ]

    result = await provider.fetch_supported_networks()

    assert result["success"] is True
    assert isinstance(result["data"], list)


@pytest.mark.asyncio
async def test_fetch_data_plans_success(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(200, {"success": True, "data": [{"provider_name": "MTN", "provider_code": "mtn-data"}]}),
        _FakeResponse(
            200,
            {
                "success": True,
                "data": [
                    {"package_name": "1GB", "package_api_code": "DATA-01", "provider_code": "mtn-data"}
                ],
            },
        ),
    ]

    result = await provider.fetch_data_plans(network="MTN")

    assert result["success"] is True
    assert result["data"][0]["package_api_code"] == "DATA-01"


@pytest.mark.asyncio
async def test_subscribe_tv_requires_provider_code_and_package_code(provider: AidaPayProvider) -> None:
    with pytest.raises(ProviderUnavailableError):
        await provider.subscribe_tv(
            smart_card_number="1234567890",
            package="legacy-unused",
            amount=5000,
            reference="REF-TV-001",
        )


@pytest.mark.asyncio
async def test_subscribe_tv_success_with_explicit_provider_and_package_codes(
    provider: AidaPayProvider,
    patch_async_client: _FakeAsyncClient,
) -> None:
    patch_async_client.queue = [
        _FakeResponse(
            200,
            {
                "success": True,
                "message": "success",
                "data": {"success": True, "transaction_data": {"transaction_hash": "TX-TV-001"}},
            },
        ),
    ]

    result = await provider.subscribe_tv(
        smart_card_number="1234567890",
        package="legacy-unused",
        amount=5000,
        reference="REF-TV-002",
        provider_code="dstv",
        package_code="dstv-compact",
    )

    assert result["success"] is True
    buy_call = patch_async_client.calls[-1]
    assert buy_call["json"]["provider_code"] == "dstv"
    assert buy_call["json"]["package_code"] == "dstv-compact"


@pytest.mark.asyncio
async def test_timeout_raises_temporary_failure(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [httpx.TimeoutException("timed out")]

    with pytest.raises(ProviderTemporaryFailure):
        await provider.check_balance()


@pytest.mark.asyncio
async def test_authentication_failure_raises_unavailable(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(401, {"success": False, "message": "Unauthorized"})
    ]

    with pytest.raises(ProviderUnavailableError):
        await provider.check_balance()


@pytest.mark.asyncio
async def test_invalid_response_raises_unavailable(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [_FakeResponse(200, json_error=True)]

    with pytest.raises(ProviderUnavailableError):
        await provider.check_balance()


@pytest.mark.asyncio
async def test_provider_unavailable_response_raises_unavailable(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(403, {"success": False, "message": "Provider's packages not available"})
    ]

    with pytest.raises(ProviderUnavailableError):
        await provider.check_balance()


@pytest.mark.asyncio
async def test_maintenance_response_raises_temporary_failure(provider: AidaPayProvider, patch_async_client: _FakeAsyncClient) -> None:
    patch_async_client.queue = [
        _FakeResponse(403, {"success": False, "message": "maintenance is in progress"})
    ]

    with pytest.raises(ProviderTemporaryFailure):
        await provider.check_balance()
