from __future__ import annotations

import logging
from typing import Any

from app.services.giftcard_service import GiftCardService
from app.utils.exceptions import ValidationException


class GiftCardWebhookService:
    """Handle gift-card provider status and completion callbacks."""

    def __init__(self, *, giftcard_service: GiftCardService | None = None, logger: logging.Logger | None = None) -> None:
        self.giftcard_service = giftcard_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_cardtonic_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name="cardtonic")

    async def process_prestmit_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name="prestmit")

    async def process_status_update(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name=self._provider_name(payload))

    async def process_completion_event(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name=self._provider_name(payload))

    def _require_giftcard_service(self) -> GiftCardService:
        if self.giftcard_service is None:
            raise ValidationException("GiftCardService dependency is required for gift-card webhook processing.")
        return self.giftcard_service

    def _reference(self, payload: dict[str, Any]) -> str:
        return str(payload.get("reference") or payload.get("transaction_reference") or payload.get("data", {}).get("reference") or "")

    def _provider_name(self, payload: dict[str, Any]) -> str:
        return str(payload.get("provider_name") or payload.get("provider") or "giftcard")
