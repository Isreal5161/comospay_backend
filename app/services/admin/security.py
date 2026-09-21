from __future__ import annotations

import logging
from typing import Any
from app.utils.exceptions import ValidationException


class SecurityAdministrationService:
    """Service for security administration actions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def _review_security_event(self, **payload: Any) -> dict[str, Any]:
        raise ValidationException(detail="Security administration is unavailable.", error_code="ADMIN_SECURITY_UNAVAILABLE")
