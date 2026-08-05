from __future__ import annotations

import logging
from typing import Any


class DashboardService:
    """Service for admin dashboard and overview operations."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def get_stats(self, **payload: Any) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "users": 0,
                "transactions": 0,
                "revenue": 0,
                "pending_approvals": 0,
            },
            "meta": {"source": "admin.dashboard"},
        }
