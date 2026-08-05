from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.integrations.airtime.exceptions import (
    NoProviderAvailableError,
    ProviderTemporaryFailure,
    ProviderUnavailableError,
)
from app.integrations.airtime.manager import ProviderManager
from app.integrations.airtime.registry import ProviderRegistry


class _StubProvider:
    def __init__(self, *, provider_name: str, provider_priority: int, action=None) -> None:
        self._provider_name = provider_name
        self._provider_priority = provider_priority
        self._action = action

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def priority(self) -> int:
        return self._provider_priority

    @property
    def is_available(self) -> bool:
        return True

    async def buy_airtime(self, **kwargs):
        if self._action is None:
            return {"provider": self.name, "status": "ok", "payload": kwargs}
        return await self._action(**kwargs)


def test_settings_supports_provider_base_urls_and_keys() -> None:
    loaded = Settings(
        aidapay_api_key="aidapay-token",
        aidapay_base_url="https://api.aidapay.example",
        vtugate_api_key="vtugate-token",
        vtugate_base_url="https://api.vtugate.example",
        vtung_api_key="vtung-token",
        vtung_base_url="https://api.vtung.example",
        clubconnect_api_key="clubconnect-token",
        clubconnect_base_url="https://api.clubconnect.example",
    )

    assert loaded.aidapay_api_key is not None
    assert loaded.aidapay_base_url == "https://api.aidapay.example"
    assert loaded.vtugate_api_key is not None
    assert loaded.vtugate_base_url == "https://api.vtugate.example"
    assert loaded.vtung_api_key is not None
    assert loaded.vtung_base_url == "https://api.vtung.example"
    assert loaded.clubconnect_api_key is not None
    assert loaded.clubconnect_base_url == "https://api.clubconnect.example"


def test_registry_ignores_disabled_provider_without_instantiating() -> None:
    registry = ProviderRegistry()
    disabled_factory_calls = {"count": 0}

    def disabled_factory():
        disabled_factory_calls["count"] += 1
        return _StubProvider(provider_name="disabled", provider_priority=99)

    registry.register_provider(
        name="disabled",
        priority=99,
        api_key="",
        factory=disabled_factory,
    )

    enabled = registry.get_enabled_providers()

    assert enabled == []
    assert disabled_factory_calls["count"] == 0


def test_registry_returns_enabled_providers_sorted_by_priority() -> None:
    registry = ProviderRegistry()
    registry.register_provider(
        name="p2",
        priority=20,
        api_key="token-2",
        factory=lambda: _StubProvider(provider_name="p2", provider_priority=20),
    )
    registry.register_provider(
        name="p1",
        priority=10,
        api_key="token-1",
        factory=lambda: _StubProvider(provider_name="p1", provider_priority=10),
    )

    enabled = registry.get_enabled_providers()

    assert [provider.name for provider in enabled] == ["p1", "p2"]


@pytest.mark.asyncio
async def test_provider_manager_fails_over_and_returns_first_success() -> None:
    registry = ProviderRegistry()

    async def unavailable(**kwargs):
        raise ProviderUnavailableError("unavailable")

    async def temporary_failure(**kwargs):
        raise ProviderTemporaryFailure("temporary")

    async def successful(**kwargs):
        return {"status": "ok", "provider": "third", "payload": kwargs}

    registry.register_provider(
        name="first",
        priority=10,
        api_key="token",
        factory=lambda: _StubProvider(provider_name="first", provider_priority=10, action=unavailable),
    )
    registry.register_provider(
        name="second",
        priority=20,
        api_key="token",
        factory=lambda: _StubProvider(provider_name="second", provider_priority=20, action=temporary_failure),
    )
    registry.register_provider(
        name="third",
        priority=30,
        api_key="token",
        factory=lambda: _StubProvider(provider_name="third", provider_priority=30, action=successful),
    )

    manager = ProviderManager(registry=registry)
    result = await manager.execute("buy_airtime", phone_number="08000000000")

    assert result["status"] == "ok"
    assert result["provider"] == "third"


@pytest.mark.asyncio
async def test_provider_manager_raises_when_all_enabled_providers_fail() -> None:
    registry = ProviderRegistry()

    async def unavailable(**kwargs):
        raise ProviderUnavailableError("unavailable")

    async def temporary_failure(**kwargs):
        raise ProviderTemporaryFailure("temporary")

    registry.register_provider(
        name="first",
        priority=10,
        api_key="token",
        factory=lambda: _StubProvider(provider_name="first", provider_priority=10, action=unavailable),
    )
    registry.register_provider(
        name="second",
        priority=20,
        api_key="token",
        factory=lambda: _StubProvider(provider_name="second", provider_priority=20, action=temporary_failure),
    )

    manager = ProviderManager(registry=registry)

    with pytest.raises(NoProviderAvailableError):
        await manager.execute("buy_airtime", phone_number="08000000000")
