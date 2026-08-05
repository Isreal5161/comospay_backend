from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SMSProvider(ABC):
    """Abstract contract for SMS service providers."""

    @abstractmethod
    async def send_sms(
        self,
        *,
        recipient: str,
        message: str,
        reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a single SMS message."""

    @abstractmethod
    async def send_bulk_sms(
        self,
        *,
        recipients: list[str],
        message: str,
        reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a batch of SMS messages."""

    @abstractmethod
    async def get_delivery_status(self, *, provider_reference: str | None = None) -> dict[str, Any]:
        """Retrieve the delivery status of an SMS message."""

    @abstractmethod
    async def get_account_balance(self) -> dict[str, Any]:
        """Retrieve the provider account balance for SMS usage."""

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return the provider availability status."""
