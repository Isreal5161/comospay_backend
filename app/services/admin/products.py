from __future__ import annotations

import logging
from typing import Any
from app.utils.exceptions import ValidationException


class ProductAdministrationService:
    """Service for product and service catalog administration."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _list_products(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Product administration is unavailable.", error_code="ADMIN_PRODUCTS_UNAVAILABLE")
