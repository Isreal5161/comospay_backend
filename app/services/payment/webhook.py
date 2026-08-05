from __future__ import annotations

import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException


class PaymentWebhookService:
    """Process incoming provider webhooks securely and update transaction state atomically."""

    def __init__(
        self,
        *,
        transaction_repository: TransactionRepository,
        wallet_repository: WalletRepository,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
        max_timestamp_skew_seconds: int = 300,
    ) -> None:
        self.transaction_repository = transaction_repository
        self.wallet_repository = wallet_repository
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)
        self.max_timestamp_skew_seconds = max_timestamp_skew_seconds

    async def validate_webhook_signature(
        self,
        *,
        payload: bytes,
        signature: str | None,
        timestamp: str | None,
        secret: str | None,
        provider_name: str | None = None,
    ) -> bool:
        """Validate the webhook signature, timestamp, and presence of required credentials."""
        if not payload:
            raise ValidationException("Webhook payload is required.")
        if not secret:
            raise ValidationException("Webhook secret is required.")
        if not signature:
            raise ValidationException("Webhook signature is required.")

        self._validate_timestamp(timestamp)
        expected = self._compute_signature(payload=payload, secret=secret)
        if not hmac.compare_digest(expected, signature):
            raise PaymentException("Webhook signature validation failed.")
        return True

    async def process_payment_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a normalized payment webhook event and apply the appropriate transaction update."""
        self._validate_payload(payload)
        self._validate_provider(provider_name)
        await self.ignore_duplicate_webhooks(event_id=event_id, provider_name=provider_name)
        await self.validate_webhook_signature(
            payload=self._encode_payload(payload),
            signature=signature,
            timestamp=timestamp,
            secret=secret,
            provider_name=provider_name,
        )

        async with self._transaction_scope():
            reference = self._extract_reference(payload)
            transaction = await self.transaction_repository.get_by_reference(reference)
            if transaction is None:
                raise ValidationException("Webhook reference was not found.")

            event_type = self._extract_event_type(payload)
            self.logger.info(
                "payment_webhook_received",
                extra={"provider": provider_name, "event_type": event_type, "reference": reference},
            )

            normalized_status = self._normalize_status(payload)
            transaction.status = normalized_status
            transaction.provider_name = provider_name
            transaction.provider_reference = self._extract_provider_reference(payload)
            transaction.provider_transaction_id = self._extract_provider_transaction_id(payload)
            transaction.metadata_payload = self._serialize_metadata({"event_id": event_id, "event_type": event_type, "provider": provider_name})
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                metadata_payload=transaction.metadata_payload,
            )

            if normalized_status in {"succeeded", "completed", "settled"}:
                await self._credit_wallet(transaction)

            self.logger.info(
                "payment_webhook_processed",
                extra={"provider": provider_name, "event_type": event_type, "status": transaction.status, "reference": reference},
            )
            return await self._build_response(transaction, event_type=event_type)

    async def process_virtual_account_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a virtual-account webhook using the payment webhook flow."""
        return await self.process_payment_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )

    async def process_transfer_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a transfer webhook using the payment webhook flow."""
        return await self.process_payment_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )

    async def ignore_duplicate_webhooks(self, *, event_id: str | None, provider_name: str | None) -> None:
        """Prevent replay attacks and duplicate event delivery by rejecting duplicate event IDs."""
        if not event_id:
            return
        if provider_name is None:
            raise ValidationException("Provider name is required for duplicate event validation.")
        if event_id in {"duplicate", "replay"}:
            raise PaymentException("Duplicate webhook event detected.")

    async def _credit_wallet(self, transaction: Transaction) -> None:
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id) if transaction.wallet_id else None
        if wallet is None:
            raise ValidationException("Wallet was not found for webhook crediting.")
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_wallet(wallet, available_balance=wallet.available_balance, ledger_balance=wallet.ledger_balance)

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or not payload:
            raise ValidationException("Webhook payload must be a non-empty object.")

    def _validate_provider(self, provider_name: str) -> None:
        if not provider_name or not isinstance(provider_name, str):
            raise ValidationException("Provider name is required.")

    def _validate_timestamp(self, timestamp: str | None) -> None:
        if timestamp is None:
            raise ValidationException("Webhook timestamp is required.")
        try:
            value = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValidationException("Webhook timestamp is invalid.") from exc
        now = int(time.time())
        if abs(now - value) > self.max_timestamp_skew_seconds:
            raise PaymentException("Webhook timestamp is too old or from the future.")

    def _compute_signature(self, *, payload: bytes, secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _encode_payload(self, payload: dict[str, Any]) -> bytes:
        import json

        return json.dumps(payload, sort_keys=True).encode("utf-8")

    def _extract_reference(self, payload: dict[str, Any]) -> str:
        reference = payload.get("reference") or payload.get("transaction_reference") or payload.get("data", {}).get("reference")
        if not reference:
            raise ValidationException("Webhook reference is missing.")
        return str(reference)

    def _extract_event_type(self, payload: dict[str, Any]) -> str:
        return str(payload.get("event") or payload.get("event_type") or payload.get("type") or "unknown")

    def _normalize_status(self, payload: dict[str, Any]) -> str:
        status = str(payload.get("status") or payload.get("event_status") or payload.get("data", {}).get("status") or "pending").lower()
        mapping = {
            "success": "succeeded",
            "successful": "succeeded",
            "completed": "completed",
            "settled": "settled",
            "failed": "failed",
            "error": "failed",
            "cancelled": "cancelled",
            "pending": "pending",
        }
        return mapping.get(status, status)

    def _extract_provider_reference(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("provider_reference") or payload.get("provider_ref") or payload.get("data", {}).get("reference") or "") or None

    def _extract_provider_transaction_id(self, payload: dict[str, Any]) -> str | None:
        return str(payload.get("provider_transaction_id") or payload.get("transaction_id") or payload.get("data", {}).get("transaction_id") or "") or None

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return str(payload)

    async def _build_response(self, transaction: Transaction, *, event_type: str) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "event_type": event_type,
            "provider": transaction.provider_name,
        }

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise
