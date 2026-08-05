from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GiftCardProvider(ABC):
    """Abstract contract for gift card provider integrations."""

    @abstractmethod
    async def get_supported_cards(self) -> list[dict[str, Any]]:
        """Return the list of cards supported by the provider."""

    @abstractmethod
    async def get_exchange_rate(
        self,
        *,
        card_type: str,
        country: str | None = None,
        currency: str | None = None,
        amount: float | int | None = None,
    ) -> dict[str, Any]:
        """Return an exchange rate response for a requested card."""

    @abstractmethod
    async def verify_card(self, *, card_data: dict[str, Any]) -> dict[str, Any]:
        """Verify a card payload before processing."""

    @abstractmethod
    async def submit_card(self, *, card_data: dict[str, Any]) -> dict[str, Any]:
        """Submit a card for provider processing."""

    @abstractmethod
    async def get_transaction_status(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Retrieve the provider transaction status."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return the provider health status."""
