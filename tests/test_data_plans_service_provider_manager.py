from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.integrations.airtime.exceptions import ProviderTemporaryFailure, ProviderUnavailableError
from app.services.data.plans import DataPlanService
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
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._error = error

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return {
            "provider": "aidapay",
            "data": {
                "plans": [
                    {
                        "id": "mtn-1gb",
                        "code": "mtn-1gb",
                        "name": "MTN 1GB",
                        "network": "MTN",
                        "price": "1000",
                        "metadata": {"source": "provider"},
                    },
                    {
                        "id": "mtn-2gb",
                        "code": "mtn-2gb",
                        "name": "MTN 2GB",
                        "network": "MTN",
                        "price": "2000",
                    },
                ],
                "metadata": {"provider_version": "1.0"},
            },
        }


@pytest.mark.asyncio
async def test_data_plan_service_executes_provider_manager_fetch_data_plans() -> None:
    redis_client = FakeRedis()
    provider_manager = FakeProviderManager()
    service = DataPlanService(
        provider_service=SimpleNamespace(),
        provider_manager=provider_manager,
        redis_client=redis_client,
    )

    plans = await service.get_data_plans(network="MTN", provider_name="AidaPay", force_refresh=True)

    assert provider_manager.calls[0]["operation"] == "fetch_data_plans"
    assert provider_manager.calls[0]["network"] == "MTN"
    assert plans == [
        {
            "id": "mtn-1gb",
            "code": "mtn-1gb",
            "name": "MTN 1GB",
            "network": "MTN",
            "provider_name": "AidaPay",
            "price": "1000",
            "description": None,
            "validity_period": None,
            "currency": "NGN",
            "metadata": {"source": "provider"},
        },
        {
            "id": "mtn-2gb",
            "code": "mtn-2gb",
            "name": "MTN 2GB",
            "network": "MTN",
            "provider_name": "AidaPay",
            "price": "2000",
            "description": None,
            "validity_period": None,
            "currency": "NGN",
        },
    ]
    assert json.loads(redis_client.store["data:plans:mtn:aidapay"]) == plans


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("provider unavailable"), ProviderTemporaryFailure("provider temporary failure")],
)
async def test_data_plan_service_provider_failure_preserves_cache_behavior(error: Exception) -> None:
    redis_client = FakeRedis()
    success_manager = FakeProviderManager()
    service = DataPlanService(
        provider_service=SimpleNamespace(),
        provider_manager=success_manager,
        redis_client=redis_client,
    )

    cached_plans = await service.get_data_plans(network="MTN", force_refresh=True)
    assert cached_plans[0]["id"] == "mtn-1gb"

    failing_manager = FakeProviderManager(error=error)
    service.provider_manager = failing_manager

    with pytest.raises(ProviderException):
        await service.get_data_plans(network="MTN", force_refresh=True)

    cached_again = await service.get_data_plans(network="MTN")
    assert cached_again == cached_plans
    assert redis_client.store["data:plans:mtn"]


def test_data_plan_service_does_not_import_concrete_providers() -> None:
    source = Path(r"c:\Users\HP\Downloads\CosmozPay\CosmozPay-Backend\app\services\data\plans.py").read_text(encoding="utf-8")

    assert "AidaPayProvider" not in source
    assert "VTUNG" not in source
    assert "VTUGate" not in source
    assert "ClubKonnect" not in source
