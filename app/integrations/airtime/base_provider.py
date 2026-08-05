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
        provider_code: str,
        package: str,
        package_code: str,
        amount: float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Process a TV subscription."""

    @abstractmethod
    async def verify_transaction(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Verify the status of a provider transaction."""

    @abstractmethod
    async def fetch_data_plans(self, *, network: str) -> dict[str, Any]:
        """Fetch available data plans for a network."""

    @abstractmethod
    async def fetch_electricity_providers(self) -> dict[str, Any]:
        """Fetch available electricity providers."""

    @abstractmethod
    async def verify_electricity(self, *, meter_number: str, provider: str) -> dict[str, Any]:
        """Verify electricity meter details."""

    @abstractmethod
    async def fetch_cable_tv_providers(self) -> dict[str, Any]:
        """Fetch available cable TV providers."""

    @abstractmethod
    async def fetch_cable_tv_bouquets(self, *, provider_code: str) -> dict[str, Any]:
        """Fetch cable TV bouquets for a provider."""

    @abstractmethod
    async def verify_cable_tv(self, *, smart_card_number: str, provider_code: str, phone: str) -> dict[str, Any]:
        """Verify cable TV subscription details."""

    @abstractmethod
    async def get_education_price(self, *, service_id: str | int) -> dict[str, Any]:
        """Fetch the education pin price for a service."""

    @abstractmethod
    async def buy_education_pins(
        self,
        *,
        service_id: str | int,
        phone: str,
        quantity: int,
        product_code: str,
    ) -> dict[str, Any]:
        """Purchase education pins."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Report whether the provider is available for use."""
