from __future__ import annotations

import logging
from typing import Any


class MerchantService:
    """Service for merchant administration workflows."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def list_merchants(self, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": [], "meta": {"source": "admin.merchants"}}
