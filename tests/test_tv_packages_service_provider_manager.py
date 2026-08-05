from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError
from app.services.tv.packages import TVPackageService
from app.utils.exceptions import ProviderException


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.delete_calls: list[str] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        self.set_calls.append({"key": key, "value": value, "ex": ex})
        return True

    async def delete(self, key: str):
        self.delete_calls.append(key)
        self.store.pop(key, None)
        return 1


class FakeProviderManager:
    def __init__(self, responses=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._responses = responses or {}
        self._error = error

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        if self._error is not None:
            raise self._error
        response = self._responses.get(operation)
        if response is not None:
            return response(**kwargs) if callable(response) else response
        if operation == "fetch_cable_tv_providers":
            return {
                "provider": "aidapay",
                "data": [
                    {"provider_code": "DSTV", "provider_name": "DSTV"},
                    {"provider_code": "GOTV", "provider_name": "GOtv"},
                ],
                "metadata": {"source": "providers"},
            }
        if operation == "fetch_cable_tv_bouquets":
            provider_code = kwargs["provider_code"]
            return {
                "provider": "aidapay",
                "data": {
                    "packages": [
                        {
                            "id": f"{provider_code.lower()}-premium",
                            "code": f"{provider_code.lower()}-premium",
                            "name": f"{provider_code} Premium",
                            "provider_name": provider_code,
                            "price": "3500",
                            "metadata": {"provider_code": provider_code},
                        },
                        {
                            "id": f"{provider_code.lower()}-basic",
                            "code": f"{provider_code.lower()}-basic",
                            "name": f"{provider_code} Basic",
                            "provider_name": provider_code,
                            "price": "1500",
                        },
                    ],
                    "metadata": {"source": f"bouquets:{provider_code}"},
                },
            }
        raise AssertionError(f"Unexpected operation: {operation}")


@pytest.mark.asyncio
async def test_tv_package_service_executes_provider_manager_provider_and_bouquet_calls() -> None:
    redis_client = FakeRedis()
    provider_manager = FakeProviderManager()
    service = TVPackageService(
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        redis_client=redis_client,
    )

    packages = await service.get_tv_packages(provider_name="DSTV", force_refresh=True)

    assert provider_manager.calls[0]["operation"] == "fetch_cable_tv_providers"
    assert provider_manager.calls[1]["operation"] == "fetch_cable_tv_bouquets"
    assert provider_manager.calls[1]["provider_code"] == "DSTV"
    assert packages == [
        {
            "id": "dstv-premium",
            "code": "dstv-premium",
            "name": "DSTV Premium",
            "provider_name": "DSTV",
            "price": "3500",
            "currency": "NGN",
            "duration": None,
            "service_type": None,
            "description": None,
            "status": "active",
            "metadata": {"source": "providers"},
        },
        {
            "id": "dstv-basic",
            "code": "dstv-basic",
            "name": "DSTV Basic",
            "provider_name": "DSTV",
            "price": "1500",
            "currency": "NGN",
            "duration": None,
            "service_type": None,
            "description": None,
            "status": "active",
            "metadata": {"source": "providers"},
        },
    ]
    assert json.loads(redis_client.store["tv:packages:dstv"]) == [
        {
            "id": "dstv-premium",
            "code": "dstv-premium",
            "name": "DSTV Premium",
            "provider_name": "DSTV",
            "price": "3500",
            "currency": "NGN",
            "duration": None,
            "service_type": None,
            "description": None,
            "status": "active",
        },
        {
            "id": "dstv-basic",
            "code": "dstv-basic",
            "name": "DSTV Basic",
            "provider_name": "DSTV",
            "price": "1500",
            "currency": "NGN",
            "duration": None,
            "service_type": None,
            "description": None,
            "status": "active",
        },
    ]


@pytest.mark.asyncio
async def test_tv_package_service_cache_hit_returns_cached_packages_without_provider_call() -> None:
    redis_client = FakeRedis()
    cached = [
        {
            "id": "dstv-premium",
            "code": "dstv-premium",
            "name": "DSTV Premium",
            "provider_name": "DSTV",
            "price": "3500",
            "currency": "NGN",
            "duration": None,
            "service_type": None,
            "description": None,
            "status": "active",
        }
    ]
    redis_client.store["tv:packages:dstv"] = json.dumps(cached)
    provider_manager = FakeProviderManager()
    service = TVPackageService(
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        redis_client=redis_client,
    )

    result = await service.get_tv_packages(provider_name="DSTV")

    assert result == cached
    assert provider_manager.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("provider unavailable"), ProviderTemporaryFailure("provider temporary failure")],
)
async def test_tv_package_service_provider_failure_preserves_cache_behavior(error: Exception) -> None:
    redis_client = FakeRedis()
    successful_manager = FakeProviderManager()
    service = TVPackageService(
        provider_service=SimpleNamespace(),
        provider_manager=successful_manager,
        redis_client=redis_client,
    )

    cached = await service.get_tv_packages(provider_name="DSTV", force_refresh=True)
    assert cached

    failing_manager = FakeProviderManager(error=error)
    service.provider_manager = failing_manager

    with pytest.raises(ProviderException):
        await service.get_tv_packages(provider_name="DSTV", force_refresh=True)

    assert json.loads(redis_client.store["tv:packages:dstv"])


def test_tv_package_service_does_not_import_concrete_providers() -> None:
    source = Path(r"c:\Users\HP\Downloads\CosmozPay\CosmozPay-Backend\app\services\tv\packages.py").read_text(encoding="utf-8")

    assert "AidaPayProvider" not in source
    assert "VTUNG" not in source
    assert "VTUGate" not in source
    assert "ClubKonnect" not in source
