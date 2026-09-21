from __future__ import annotations

import logging
from typing import Any
from app.utils.exceptions import ValidationException


class SupportAdministrationService:
    """Service for support ticket workflows."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _list_support_tickets(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Support administration is unavailable.", error_code="ADMIN_SUPPORT_UNAVAILABLE")
