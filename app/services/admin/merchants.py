from __future__ import annotations

import logging
from typing import Any
from app.utils.exceptions import ValidationException


class MerchantService:
    """Service for merchant administration workflows."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _list_merchants(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Merchant administration is unavailable.", error_code="ADMIN_MERCHANTS_UNAVAILABLE")
