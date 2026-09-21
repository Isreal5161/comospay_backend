from __future__ import annotations

import logging
from typing import Any

from app.repositories.provider_repository import ProviderRepository
from app.services.giftcard_service import GiftCardService
from app.utils.exceptions import ValidationException


class GiftCardWebhookService:
    """Handle gift-card provider status and completion callbacks."""

    def __init__(
        self,
        *,
        giftcard_service: GiftCardService | None = None,
        provider_repository: ProviderRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.giftcard_service = giftcard_service
        self.provider_repository = provider_repository
        self.logger = logger or logging.getLogger(__name__)

    async def process_webhook(
        self,
        *,
        provider_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and process a normalized webhook for any Gift Card provider."""
        self._require_giftcard_service()
        normalized_provider = self._normalize_provider_name(provider_name)
        await self._require_active_provider(normalized_provider)
        reference = await self._resolve_transaction_reference(
            provider_name=normalized_provider,
            payload=payload,
        )
        if not reference:
            raise ValidationException("Gift card webhook transaction reference is required.")
        return await self.giftcard_service.reconcile_transaction(
            reference=reference,
            provider_name=normalized_provider,
        )

    async def process_cardtonic_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name="cardtonic")

    async def process_prestmit_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name="prestmit")

    async def process_status_update(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.process_webhook(provider_name=self._provider_name(payload), payload=payload)

    async def process_completion_event(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_giftcard_service()
        return await self.giftcard_service.reconcile_transaction(reference=self._reference(payload), provider_name=self._provider_name(payload))

    def _require_giftcard_service(self) -> GiftCardService:
        if self.giftcard_service is None:
            raise ValidationException("GiftCardService dependency is required for gift-card webhook processing.")
        return self.giftcard_service

    async def _resolve_transaction_reference(self, *, provider_name: str, payload: dict[str, Any]) -> str:
        reference = self._reference(payload)
        if reference:
            return reference

        provider_reference = self._provider_reference(payload)
        if not provider_reference:
            return ""

        reconciliation_service = getattr(self.giftcard_service, "reconciliation_service", None)
        repository = getattr(reconciliation_service, "transaction_repository", None)
        if repository is None:
            return ""

        transaction = await repository.get_by_provider_reference(
            provider_name=provider_name,
            provider_reference=provider_reference,
        )
        if transaction is None:
            return ""
        return transaction.reference

    def _reference(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("reference")
            or payload.get("transaction_reference")
            or payload.get("internal_reference")
            or payload.get("data", {}).get("reference")
            or payload.get("data", {}).get("transaction_reference")
            or ""
        )

    def _provider_reference(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("provider_reference")
            or payload.get("provider_ref")
            or payload.get("transaction_id")
            or payload.get("transaction_ref")
            or payload.get("data", {}).get("provider_reference")
            or payload.get("data", {}).get("provider_ref")
            or ""
        )

    def _provider_name(self, payload: dict[str, Any]) -> str:
        return str(payload.get("provider_name") or payload.get("provider") or "giftcard")

    def _normalize_provider_name(self, provider_name: str) -> str:
        normalized = str(provider_name or "").strip().lower()
        if not normalized:
            raise ValidationException("Gift card webhook provider is required.")
        return normalized

    async def _require_active_provider(self, provider_name: str) -> None:
        if self.provider_repository is None:
            raise ValidationException("Gift card webhook provider repository is required.")
        providers = await self.provider_repository.get_active_providers(category="Gift Cards")
        for provider in providers:
            if provider.code.strip().lower() == provider_name or provider.name.strip().lower() == provider_name:
                if provider.status.lower() in {"active", "healthy", "ready"}:
                    return
                break
        raise ValidationException("Gift card webhook provider is unknown or inactive.")
