from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import SecretStr

from app.config.settings import Settings, settings
from app.integrations.airtime.aidapay import AidaPayProvider
from app.integrations.airtime.base_provider import VTUProvider
from app.integrations.airtime.clubkonnect import ClubConnectProvider
from app.integrations.airtime.vtugate import VTUGateProvider
from app.integrations.airtime.vtung import VTUNGProvider


def _has_secret(value: SecretStr | str | None) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    return bool(str(value).strip())


@dataclass(slots=True)
class ProviderRegistration:
    name: str
    priority: int
    api_key: SecretStr | str | None
    factory: Callable[[], VTUProvider]


class ProviderRegistry:
    """Registry for credential-aware provider registration and enablement."""

    def __init__(self) -> None:
        self._registrations: list[ProviderRegistration] = []

    def register_provider(
        self,
        *,
        name: str,
        priority: int,
        api_key: SecretStr | str | None,
        factory: Callable[[], VTUProvider],
    ) -> None:
        self._registrations.append(
            ProviderRegistration(
                name=name,
                priority=priority,
                api_key=api_key,
                factory=factory,
            )
        )

    def get_enabled_providers(self) -> list[VTUProvider]:
        """Return enabled providers sorted by ascending priority."""
        enabled: list[VTUProvider] = []

        for registration in sorted(self._registrations, key=lambda item: item.priority):
            if not _has_secret(registration.api_key):
                continue
            provider = registration.factory()
            if provider.is_available:
                enabled.append(provider)

        return enabled


def build_vtu_provider_registry(*, settings_obj: Settings = settings) -> ProviderRegistry:
    """Build the default VTU provider registry from application settings."""
    registry = ProviderRegistry()

    registry.register_provider(
        name="aidapay",
        priority=10,
        api_key=settings_obj.aidapay_api_key,
        factory=lambda: AidaPayProvider(api_key=settings_obj.aidapay_api_key, base_url=settings_obj.aidapay_base_url),
    )
    registry.register_provider(
        name="vtugate",
        priority=20,
        api_key=settings_obj.vtugate_api_key,
        factory=lambda: VTUGateProvider(api_key=settings_obj.vtugate_api_key, base_url=settings_obj.vtugate_base_url),
    )
    registry.register_provider(
        name="vtung",
        priority=30,
        api_key=settings_obj.vtung_api_key,
        factory=lambda: VTUNGProvider(api_key=settings_obj.vtung_api_key, base_url=settings_obj.vtung_base_url),
    )

    clubconnect_key = settings_obj.clubconnect_api_key or settings_obj.clubkonnect_api_key
    registry.register_provider(
        name="clubconnect",
        priority=40,
        api_key=clubconnect_key,
        factory=lambda: ClubConnectProvider(api_key=clubconnect_key, base_url=settings_obj.clubconnect_base_url),
    )

    return registry
