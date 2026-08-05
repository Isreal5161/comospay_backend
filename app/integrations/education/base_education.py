from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EducationProvider(ABC):
    """Abstract contract for education service providers."""

    @abstractmethod
    async def get_available_products(self) -> list[dict[str, Any]]:
        """Return the list of products supported by the provider."""

    @abstractmethod
    async def purchase_pin(
        self,
        *,
        product_code: str,
        quantity: int = 1,
        customer: dict[str, Any] | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Purchase an examination PIN from the provider."""

    @abstractmethod
    async def verify_candidate(
        self,
        *,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        """Verify a candidate record where supported by the provider."""

    @abstractmethod
    async def get_transaction_status(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Retrieve the status of a transaction from the provider."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return the provider availability status."""
