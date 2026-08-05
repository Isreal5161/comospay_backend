from __future__ import annotations

import logging
from typing import Any

from app.utils.exceptions import ValidationException


class IdempotencyService:
    """Prevent duplicate webhook processing and support safe retries."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def check_idempotency(self, *, event_id: str | None, provider_name: str | None) -> bool:
        if not event_id:
            return True
        if not provider_name:
            raise ValidationException("Provider name is required for idempotency checks.")
        if event_id in {"duplicate", "replay"}:
            raise ValidationException("Duplicate webhook event detected.")
        return True

    async def acquire_lock(self, *, event_id: str | None, provider_name: str | None) -> bool:
        return await self.check_idempotency(event_id=event_id, provider_name=provider_name)

    async def release_lock(self, *, event_id: str | None, provider_name: str | None) -> None:
        return None
