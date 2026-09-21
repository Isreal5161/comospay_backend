from __future__ import annotations

import logging
from typing import Any
from app.utils.exceptions import ValidationException


class MarketingService:
    """Service for marketing campaigns and promotions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _list_campaigns(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Marketing administration is unavailable.", error_code="ADMIN_MARKETING_UNAVAILABLE")
