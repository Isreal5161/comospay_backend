from __future__ import annotations

import logging
from typing import Any


class MarketingService:
    """Service for marketing campaigns and promotions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def list_campaigns(self, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": [], "meta": {"source": "admin.marketing"}}
