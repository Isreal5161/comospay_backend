from __future__ import annotations

import logging
from typing import Any


class FraudService:
    """Service for fraud monitoring and review actions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def review_fraud_signal(self, **payload: Any) -> dict[str, Any]:
        return {"success": True, "data": payload, "meta": {"source": "admin.fraud"}}
