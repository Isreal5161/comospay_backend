from __future__ import annotations

from typing import Any

from app.integrations.airtime.exceptions import (
    NoProviderAvailableError,
    ProviderTemporaryFailure,
    ProviderUnavailableError,
)
from app.integrations.airtime.registry import ProviderRegistry, build_vtu_provider_registry


class ProviderManager:
    """Execute VTU operations with automatic provider failover."""

    def __init__(self, *, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or build_vtu_provider_registry()

    def get_enabled_providers(self) -> list[Any]:
        """Retrieve provider instances that are enabled by credential availability."""
        return self.registry.get_enabled_providers()

    async def execute(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        """Attempt providers in priority order until one succeeds."""
        providers = self.get_enabled_providers()
        if not providers:
            raise NoProviderAvailableError("No enabled providers are configured.")

        last_error: Exception | None = None

        for provider in providers:
            provider_operation = getattr(provider, operation, None)
            if provider_operation is None:
                continue

            try:
                return await provider_operation(**kwargs)
            except (ProviderUnavailableError, ProviderTemporaryFailure) as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise NoProviderAvailableError(str(last_error)) from last_error

        raise NoProviderAvailableError("No enabled provider supports the requested operation.")
