from __future__ import annotations

import logging
from typing import Any

from app.repositories.provider_repository import ProviderRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.ledger_service import LedgerService


class FinanceService:
    """Service for finance and revenue administration views."""

    def __init__(
        self,
        *,
        ledger_service: LedgerService | None = None,
        transaction_repository: TransactionRepository | None = None,
        provider_repository: ProviderRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ledger_service = ledger_service
        self.transaction_repository = transaction_repository
        self.provider_repository = provider_repository
        self.logger = logger or logging.getLogger(__name__)

    async def get_platform_statistics(self, **payload: Any) -> dict[str, Any]:
        ledger_statistics = {}
        if self.ledger_service is not None:
            ledger_statistics = await self.ledger_service.get_statistics()

        transaction_count = 0
        if self.transaction_repository is not None:
            _, transaction_count = await self.transaction_repository.get_admin_transactions(page=1, page_size=1)

        provider_count = 0
        active_provider_count = 0
        if self.provider_repository is not None:
            providers, provider_count = await self.provider_repository.get_all_providers(page=1, page_size=1000)
            active_provider_count = sum(1 for provider in providers if getattr(provider, "is_active", False))

        return {
            "success": True,
            "data": {
                "ledger_statistics": ledger_statistics,
                "transaction_count": transaction_count,
                "provider_count": provider_count,
                "active_provider_count": active_provider_count,
            },
            "meta": {"source": "admin.finance"},
        }
