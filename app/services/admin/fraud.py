from __future__ import annotations

import logging

from typing import Any
from app.utils.exceptions import ValidationException


class FraudService:
    """Service for fraud monitoring and review actions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _review_fraud_signal(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Fraud administration is unavailable.", error_code="ADMIN_FRAUD_UNAVAILABLE")
