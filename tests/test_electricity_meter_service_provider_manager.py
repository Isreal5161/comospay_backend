from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.integrations.airtime.exceptions import NoProviderAvailableError
from app.services.electricity.meter import ElectricityMeterService
from app.utils.exceptions import ProviderException


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value


class FakeSettingsRepository:
    async def get_by_key(self, key: str):
        return None


class FakeProviderService:
    async def execute_electricity(self, **kwargs):
        raise AssertionError("provider_service should not be used by ElectricityMeterService")


class FakeProviderManager:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = response
        self.error = error

    async def execute(self, operation: str, **kwargs):
        self.calls.append({"operation": operation, **kwargs})
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return {
            "provider": "aidapay",
            "data": {
                "customer_name": "Ada Example",
                "meter_type": "prepaid",
                "address": "12 Main Street",
                "reference": str(uuid4()),
                "transaction_id": "meter-tx-001",
            },
        }


@pytest.mark.asyncio
async def test_electricity_meter_service_executes_provider_manager_and_normalizes_response() -> None:
    redis_client = FakeRedis()
    provider_manager = FakeProviderManager()
    service = ElectricityMeterService(
        provider_service=FakeProviderService(),
        provider_manager=provider_manager,
        settings_repository=FakeSettingsRepository(),
        redis_client=redis_client,
    )

    result = await service.verify_meter(
        meter_number=" 1234 5678 9012 ",
        disco="ikedc",
        meter_type="prepaid",
        provider_operation=lambda provider: None,
    )

    assert provider_manager.calls[0]["operation"] == "purchase_electricity"
    assert provider_manager.calls[0]["meter_number"] == "123456789012"
    assert provider_manager.calls[0]["provider"] == "IKEDC"
    assert provider_manager.calls[0]["amount"] == 0
    assert result["provider_name"] == "aidapay"
    assert result["customer_name"] == "Ada Example"
    assert result["meter_number"] == "123456789012"
    assert result["meter_type"] == "prepaid"
    assert result["address"] == "12 Main Street"
    assert result["disco"] == "IKEDC"
    assert result["raw_payload"]["customer_name"] == "Ada Example"
    assert json.loads(redis_client.store[service._cache_key(provider_name=None, disco="IKEDC", meter_number="123456789012")]) == result


@pytest.mark.asyncio
async def test_electricity_meter_service_uses_cache_on_repeat_lookup() -> None:
    redis_client = FakeRedis()
    provider_manager = FakeProviderManager()
    service = ElectricityMeterService(
        provider_service=FakeProviderService(),
        provider_manager=provider_manager,
        settings_repository=FakeSettingsRepository(),
        redis_client=redis_client,
    )

    first = await service.verify_meter(meter_number="12345678901", disco="AEDC")
    second = await service.verify_meter(meter_number="12345678901", disco="AEDC")

    assert first == second
    assert len(provider_manager.calls) == 1


@pytest.mark.asyncio
async def test_electricity_meter_service_wraps_provider_manager_failure() -> None:
    service = ElectricityMeterService(
        provider_service=FakeProviderService(),
        provider_manager=FakeProviderManager(error=NoProviderAvailableError("No enabled provider supports the requested operation.")),
        settings_repository=FakeSettingsRepository(),
    )

    with pytest.raises(ProviderException) as exc_info:
        await service.verify_meter(meter_number="12345678901", disco="PHED")

    assert "No enabled provider supports the requested operation." in str(exc_info.value.detail)
