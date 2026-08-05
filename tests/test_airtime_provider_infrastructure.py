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
        self.calls: list[dict] = []

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
        self.calls.append(kwargs)
        if self._action is None:
            return {"provider": self.name, "status": "ok", "payload": kwargs}
        return await self._action(**kwargs)


def _register_stub_provider(registry: ProviderRegistry, *, name: str, priority: int, action) -> _StubProvider:
    provider = _StubProvider(provider_name=name, provider_priority=priority, action=action)
    registry.register_provider(
        name=name,
        priority=priority,
        api_key=f"{name}-token",
        factory=lambda provider=provider: provider,
    )
    return provider


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
        name="vtung",
        priority=40,
        api_key="token-4",
        factory=lambda: _StubProvider(provider_name="vtung", provider_priority=40),
    )
    registry.register_provider(
        name="clubconnect",
        priority=30,
        api_key="token-3",
        factory=lambda: _StubProvider(provider_name="clubconnect", provider_priority=30),
    )
    registry.register_provider(
        name="vtugate",
        priority=20,
        api_key="token-2",
        factory=lambda: _StubProvider(provider_name="vtugate", provider_priority=20),
    )
    registry.register_provider(
        name="aidapay",
        priority=10,
        api_key="token-1",
        factory=lambda: _StubProvider(provider_name="aidapay", provider_priority=10),
    )

    enabled = registry.get_enabled_providers()

    assert [provider.name for provider in enabled] == ["aidapay", "vtugate", "clubconnect", "vtung"]


@pytest.mark.asyncio
async def test_provider_manager_fails_over_in_priority_order_and_returns_first_success() -> None:
    registry = ProviderRegistry()
    call_order: list[str] = []

    async def unavailable(provider_name: str, **kwargs):
        call_order.append(provider_name)
        raise ProviderUnavailableError("unavailable")

    async def temporary_failure(provider_name: str, **kwargs):
        call_order.append(provider_name)
        raise ProviderTemporaryFailure("temporary")

    async def successful(provider_name: str, **kwargs):
        call_order.append(provider_name)
        return {"status": "ok", "provider": provider_name, "payload": kwargs}

    _register_stub_provider(registry, name="aidapay", priority=10, action=lambda **kwargs: unavailable("aidapay", **kwargs))
    _register_stub_provider(registry, name="vtugate", priority=20, action=lambda **kwargs: temporary_failure("vtugate", **kwargs))
    _register_stub_provider(registry, name="clubconnect", priority=30, action=lambda **kwargs: unavailable("clubconnect", **kwargs))
    _register_stub_provider(registry, name="vtung", priority=40, action=lambda **kwargs: successful("vtung", **kwargs))

    manager = ProviderManager(registry=registry)
    result = await manager.execute("buy_airtime", phone_number="08000000000")

    assert call_order == ["aidapay", "vtugate", "clubconnect", "vtung"]
    assert result["provider"] == "vtung"
    assert result["data"]["status"] == "ok"


@pytest.mark.asyncio
async def test_provider_manager_stops_after_first_success_in_priority_order() -> None:
    registry = ProviderRegistry()
    call_order: list[str] = []

    async def successful(provider_name: str, **kwargs):
        call_order.append(provider_name)
        return {"status": "ok", "provider": provider_name, "payload": kwargs}

    _register_stub_provider(registry, name="aidapay", priority=10, action=lambda **kwargs: successful("aidapay", **kwargs))
    _register_stub_provider(registry, name="vtugate", priority=20, action=lambda **kwargs: successful("vtugate", **kwargs))
    _register_stub_provider(registry, name="clubconnect", priority=30, action=lambda **kwargs: successful("clubconnect", **kwargs))
    _register_stub_provider(registry, name="vtung", priority=40, action=lambda **kwargs: successful("vtung", **kwargs))

    manager = ProviderManager(registry=registry)
    result = await manager.execute("buy_airtime", phone_number="08000000000")

    assert call_order == ["aidapay"]
    assert result["provider"] == "aidapay"
    assert result["data"]["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_factory",
    [ProviderUnavailableError, ProviderTemporaryFailure],
)
async def test_provider_manager_retries_when_provider_raises_retryable_error(error_factory) -> None:
    registry = ProviderRegistry()
    call_order: list[str] = []

    async def retryable(provider_name: str, **kwargs):
        call_order.append(provider_name)
        raise error_factory("retryable")

    async def successful(provider_name: str, **kwargs):
        call_order.append(provider_name)
        return {"status": "ok", "provider": provider_name, "payload": kwargs}

    _register_stub_provider(registry, name="aidapay", priority=10, action=lambda **kwargs: retryable("aidapay", **kwargs))
    _register_stub_provider(registry, name="vtugate", priority=20, action=lambda **kwargs: successful("vtugate", **kwargs))
    _register_stub_provider(registry, name="clubconnect", priority=30, action=lambda **kwargs: successful("clubconnect", **kwargs))
    _register_stub_provider(registry, name="vtung", priority=40, action=lambda **kwargs: successful("vtung", **kwargs))

    manager = ProviderManager(registry=registry)
    result = await manager.execute("buy_airtime", phone_number="08000000000")

    assert call_order == ["aidapay", "vtugate"]
    assert result["provider"] == "vtugate"
    assert result["data"]["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_factory",
    [ProviderUnavailableError, ProviderTemporaryFailure],
)
async def test_provider_manager_raises_when_all_enabled_providers_fail(error_factory) -> None:
    registry = ProviderRegistry()
    call_order: list[str] = []

    async def failing(provider_name: str, **kwargs):
        call_order.append(provider_name)
        raise error_factory("failure")

    _register_stub_provider(registry, name="aidapay", priority=10, action=lambda **kwargs: failing("aidapay", **kwargs))
    _register_stub_provider(registry, name="vtugate", priority=20, action=lambda **kwargs: failing("vtugate", **kwargs))
    _register_stub_provider(registry, name="clubconnect", priority=30, action=lambda **kwargs: failing("clubconnect", **kwargs))
    _register_stub_provider(registry, name="vtung", priority=40, action=lambda **kwargs: failing("vtung", **kwargs))

    manager = ProviderManager(registry=registry)

    with pytest.raises(NoProviderAvailableError) as exc_info:
        await manager.execute("buy_airtime", phone_number="08000000000")

    assert call_order == ["aidapay", "vtugate", "clubconnect", "vtung"]
    assert "failure" in str(exc_info.value)
