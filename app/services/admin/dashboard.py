from __future__ import annotations

import logging
from typing import Any

from app.repositories.kyc_repository import KYCRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.services.ledger_service import LedgerService


class DashboardService:
    """Service for admin dashboard and overview operations."""

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        transaction_repository: TransactionRepository,
        kyc_repository: KYCRepository,
        ledger_service: LedgerService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.transaction_repository = transaction_repository
        self.kyc_repository = kyc_repository
        self.ledger_service = ledger_service
        self.logger = logger or logging.getLogger(__name__)

    async def get_stats(self, **payload: Any) -> dict[str, Any]:
        _, total_users = await self.user_repository.get_all_users(page=1, page_size=1)
        _, total_transactions = await self.transaction_repository.get_admin_transactions(page=1, page_size=1)
        _, pending_kyc = await self.kyc_repository.get_pending_kyc(page=1, page_size=1)

        ledger_statistics = {}
        if self.ledger_service is not None:
            ledger_statistics = await self.ledger_service.get_statistics()

        return {
            "success": True,
            "data": {
                "users": total_users,
                "transactions": total_transactions,
                "revenue": str(ledger_statistics.get("total_credit_amount", 0)),
                "pending_approvals": pending_kyc,
                "ledger_statistics": ledger_statistics,
            },
            "meta": {"source": "admin.dashboard"},
        }
