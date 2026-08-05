from __future__ import annotations

import logging
from typing import Any

from app.services.payment_service import PaymentService
from app.services.provider_service import ProviderService
from app.services.wallet_service import WalletService
from app.utils.exceptions import ValidationException


class FlutterwaveWebhookService:
    """Handle Flutterwave payment, transfer, and wallet webhook events."""

    def __init__(
        self,
        *,
        payment_service: PaymentService | None = None,
        wallet_service: WalletService | None = None,
        provider_service: ProviderService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.payment_service = payment_service
        self.wallet_service = wallet_service
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_payment_webhook(self, *, provider_name: str, event_id: str | None, payload: dict[str, Any], signature: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.process_payment_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )

    async def process_transfer_webhook(self, *, provider_name: str, event_id: str | None, payload: dict[str, Any], signature: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.process_transfer_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )

    async def process_virtual_account_webhook(self, *, provider_name: str, event_id: str | None, payload: dict[str, Any], signature: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.process_virtual_account_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )

    async def process_verification_webhook(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.verify_payment(reference=self._reference(payload))

    async def handle_wallet_funding_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_wallet_service()
        return await self.wallet_service.reconcile_wallet_funding(
            provider_name=self._provider_name(payload),
            provider_reference=self._provider_reference(payload),
            reference=self._reference(payload),
        )

    async def handle_refund_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.refund_payment(reference=self._reference(payload), reason=self._reason(payload))

    async def handle_chargeback_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.process_failed_payment(reference=self._reference(payload), reason=self._reason(payload))

    async def handle_settlement_event(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_payment_service()
        return await self.payment_service.reconcile_payment(
            reference=self._reference(payload),
            provider_status=self._status(payload),
        )

    def _require_payment_service(self) -> PaymentService:
        if self.payment_service is None:
            raise ValidationException("PaymentService dependency is required for Flutterwave webhook processing.")
        return self.payment_service

    def _require_wallet_service(self) -> WalletService:
        if self.wallet_service is None:
            raise ValidationException("WalletService dependency is required for Flutterwave wallet callbacks.")
        return self.wallet_service

    def _provider_name(self, payload: dict[str, Any]) -> str:
        return str(payload.get("provider_name") or payload.get("provider") or "flutterwave")

    def _provider_reference(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("provider_reference") or payload.get("provider_ref") or "") or None

    def _reference(self, payload: dict[str, Any]) -> str:
        return str(payload.get("reference") or payload.get("transaction_reference") or payload.get("data", {}).get("reference") or "")

    def _status(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("status") or payload.get("event_status") or payload.get("data", {}).get("status") or "") or None

    def _reason(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("reason") or payload.get("message") or "") or None
