from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VTUProvider(ABC):
    """Abstract contract for VTU service providers."""

    @abstractmethod
    async def check_balance(self) -> dict[str, Any]:
        """Check the provider account balance."""

    @abstractmethod
    async def buy_airtime(
        self,
        *,
        phone_number: str,
        network: str,
        amount: float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase airtime for a recipient."""

    @abstractmethod
    async def buy_data(
        self,
        *,
        phone_number: str,
        network: str,
        bundle_code: str,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase a data bundle for a recipient."""

    @abstractmethod
    async def purchase_electricity(
        self,
        *,
        meter_number: str,
        provider: str,
        amount: float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase an electricity token for a meter."""

    @abstractmethod
    async def subscribe_tv(
        self,
        *,
        smart_card_number: str,
        package: str,
        amount: float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Process a TV subscription."""

    @abstractmethod
    async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Verify the status of a provider transaction."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Report whether the provider is available for use."""
