from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PaymentProvider(ABC):
    """Abstract contract for payment provider integrations."""

    @abstractmethod
    async def initialize_payment(
        self,
        *,
        amount: float | int,
        currency: str,
        customer: dict[str, Any] | None = None,
        transaction_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a payment request with the provider."""

    @abstractmethod
    async def verify_payment(
        self,
        *,
        transaction_reference: str | None = None,
        provider_reference: str | None = None,
    ) -> dict[str, Any]:
        """Verify a payment transaction after completion."""

    @abstractmethod
    async def create_virtual_account(
        self,
        *,
        customer: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a virtual account when supported by the provider."""

    @abstractmethod
    async def transfer(
        self,
        *,
        account_details: dict[str, Any],
        amount: float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Initiate a bank transfer with the provider."""

    @abstractmethod
    async def get_transaction_status(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Retrieve the status of a transaction from the provider."""
