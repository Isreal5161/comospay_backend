from __future__ import annotations

import logging
from typing import Any

from app.services.education_service import EducationService
from app.utils.exceptions import ValidationException


class EducationWebhookService:
    """Handle education-provider webhook updates."""

    def __init__(self, *, education_service: EducationService | None = None, logger: logging.Logger | None = None) -> None:
        self.education_service = education_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_waec_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name="waec")

    async def process_neco_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name="neco")

    async def process_nabteb_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name="nabteb")

    async def process_jamb_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name="jamb")

    async def process_remita_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name="remita")

    async def process_purchase_update(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_education_service()
        return await self.education_service.reconcile_transaction(reference=self._reference(payload), provider_name=self._provider_name(payload))

    def _require_education_service(self) -> EducationService:
        if self.education_service is None:
            raise ValidationException("EducationService dependency is required for education webhook processing.")
        return self.education_service

    def _reference(self, payload: dict[str, Any]) -> str:
        return str(payload.get("reference") or payload.get("transaction_reference") or payload.get("data", {}).get("reference") or "")

    def _provider_name(self, payload: dict[str, Any]) -> str:
        return str(payload.get("provider_name") or payload.get("provider") or "education")
