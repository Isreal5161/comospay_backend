from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.repositories.transaction_repository import TransactionRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import GiftCardException, ValidationException


class GiftCardSettlementService:
    """Manage gift card settlement operations through provider orchestration."""

    CACHE_KEY_TEMPLATE = "giftcard:settlement:{provider_name}:{reference}"

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.logger = logger or logging.getLogger(__name__)

    async def settle_giftcard_transaction(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        partial_amount: Decimal | float | int | None = None,
        settlement_reference: str | None = None,
    ) -> dict[str, Any]:
        """Process settlement for a completed gift card transaction."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card settlement.")

        self._validate_settlement_eligibility(resolved)
        partial_amount_value = self._normalize_optional_amount(partial_amount)
        settlement_reference_value = settlement_reference.strip() if settlement_reference else self.generate_settlement_reference()

        self.logger.info(
            "giftcard_settlement_started",
            extra={
                "reference": resolved.reference,
                "provider_name": provider_name,
                "settlement_reference": settlement_reference_value,
                "partial_amount": str(partial_amount_value) if partial_amount_value is not None else None,
            },
        )

        async with self._transaction_scope():
            transaction_record = await self.transaction_repository.get_by_reference(resolved.reference)
            if transaction_record is None:
                raise ValidationException("Gift card transaction was not found for settlement.")

            if transaction_record.status == "settled":
                return await self._build_response(transaction_record)

            provider_response = await self._execute_settlement(
                transaction=transaction_record,
                provider_operation=provider_operation,
                provider_name=provider_name,
                partial_amount=partial_amount_value,
                settlement_reference=settlement_reference_value,
            )

            status = self._normalize_status(provider_response.get("status"))
            metadata = self._parse_metadata(transaction_record.metadata_payload)
            self._append_settlement_event(
                metadata,
                event="settlement_attempt",
                status=status,
                provider_response=provider_response,
                settlement_reference=settlement_reference_value,
                amount=partial_amount_value,
            )

            transaction_record.status = status
            transaction_record.provider_name = provider_name or provider_response.get("provider") or transaction_record.provider_name
            transaction_record.provider_reference = provider_response.get("provider_reference") or transaction_record.provider_reference
            transaction_record.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction_record.provider_transaction_id
            transaction_record.external_reference = transaction_record.external_reference or settlement_reference_value
            transaction_record.metadata_payload = self._serialize_metadata(metadata)

            transaction_record = await self.transaction_repository.update_transaction(
                transaction_record,
                status=transaction_record.status,
                provider_name=transaction_record.provider_name,
                provider_reference=transaction_record.provider_reference,
                provider_transaction_id=transaction_record.provider_transaction_id,
                external_reference=transaction_record.external_reference,
                metadata_payload=transaction_record.metadata_payload,
            )

            if status in {"failed", "cancelled", "reversed"}:
                self.logger.warning(
                    "giftcard_settlement_failed",
                    extra={"reference": transaction_record.reference, "status": status},
                )
            else:
                self.logger.info(
                    "giftcard_settlement_completed",
                    extra={"reference": transaction_record.reference, "status": status},
                )

            return await self._build_response(transaction_record)

    async def retry_giftcard_settlement(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        partial_amount: Decimal | float | int | None = None,
        settlement_reference: str | None = None,
    ) -> dict[str, Any]:
        """Retry settlement for a gift card transaction that has not yet settled."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status == "settled":
            return await self._build_response(resolved)

        return await self.settle_giftcard_transaction(
            transaction=resolved,
            provider_operation=provider_operation,
            provider_name=provider_name,
            partial_amount=partial_amount,
            settlement_reference=settlement_reference,
        )

    async def get_settlement_status(self, *, reference: str) -> dict[str, Any]:
        """Return the current settlement status for a gift card transaction."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Gift card transaction reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference.strip())
        if transaction is None:
            raise ValidationException("Gift card transaction was not found.")
        return await self._build_response(transaction)

    def generate_settlement_reference(self, prefix: str = "settlement") -> str:
        """Generate a unique settlement reference."""
        return f"giftcard-{prefix}-{uuid4().hex[:12]}"

    async def _execute_settlement(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]],
        provider_name: str | None,
        partial_amount: Decimal | None,
        settlement_reference: str,
    ) -> dict[str, Any]:
        try:
            response = await self.provider_service.execute_giftcard(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "reference": transaction.reference,
                    "provider_name": provider_name,
                    "amount": str(partial_amount) if partial_amount is not None else str(transaction.amount),
                    "currency": transaction.currency,
                    "partial_settlement_amount": str(partial_amount) if partial_amount is not None else None,
                    "settlement_reference": settlement_reference,
                    "transaction_type": transaction.transaction_type,
                    "description": transaction.description,
                },
            )
        except Exception as exc:
            raise GiftCardException(detail=str(exc)) from exc
        return response

    def _validate_settlement_eligibility(self, transaction: Transaction) -> None:
        if transaction.category != "giftcard":
            raise ValidationException("Only gift card transactions are eligible for gift card settlement.")
        if transaction.status == "settled":
            return
        if transaction.status not in {"succeeded", "completed"}:
            raise ValidationException("Only completed gift card transactions can be settled.")

    def _normalize_provider_response(self, provider_response: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(provider_response, dict):
            payload = provider_response
        else:
            payload = {"value": provider_response}
        return {
            "status": payload.get("status"),
            "provider": getattr(provider, "name", None) or self._coerce_string(payload.get("provider")),
            "provider_reference": self._coerce_string(payload.get("provider_reference") or payload.get("reference")),
            "provider_transaction_id": self._coerce_string(payload.get("provider_transaction_id") or payload.get("transaction_id")),
            "settlement_reference": self._coerce_string(payload.get("settlement_reference") or payload.get("settlement_id")),
            "message": self._coerce_string(payload.get("message") or payload.get("error")),
        }

    def _normalize_status(self, status: Any) -> str:
        if status is None:
            return "pending"
        normalized = str(status).strip().lower()
        if normalized in {"success", "succeeded", "completed", "settled", "paid"}:
            return "settled"
        if normalized in {"failed", "failure", "cancelled", "declined", "error"}:
            return "failed"
        if normalized in {"timeout", "timed_out", "time_out"}:
            return "pending"
        if normalized in {"duplicate"}:
            return "settled"
        if normalized in {"pending", "processing", "queued", "in_progress"}:
            return "pending"
        return normalized

    def _append_settlement_event(
        self,
        metadata: dict[str, Any],
        event: str,
        *,
        status: str,
        provider_response: dict[str, Any] | None = None,
        settlement_reference: str | None = None,
        amount: Decimal | None = None,
    ) -> None:
        events = metadata.get("settlement_events")
        if not isinstance(events, list):
            events = []
        event_payload: dict[str, Any] = {
            "event": event,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "settlement_reference": settlement_reference,
            "amount": str(amount) if amount is not None else None,
        }
        if provider_response is not None:
            event_payload["provider_response"] = provider_response
        events.append(event_payload)
        metadata["settlement_events"] = events
        metadata["last_settlement_status"] = status
        if settlement_reference is not None:
            metadata["settlement_reference"] = settlement_reference

    async def _resolve_transaction(self, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Gift card transaction reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference.strip())
        if resolved is None:
            raise ValidationException("Gift card transaction was not found.")
        return resolved

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required for gift card settlement.")

    def _build_response(self, transaction: Transaction) -> dict[str, Any]:
        metadata = self._parse_metadata(transaction.metadata_payload)
        return {
            "reference": transaction.reference,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "settlement_reference": metadata.get("settlement_reference"),
            "settlement_events": metadata.get("settlement_events", []),
            "metadata_payload": metadata,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }

    def _parse_metadata(self, metadata_payload: str | None) -> dict[str, Any]:
        if not metadata_payload or not isinstance(metadata_payload, str):
            return {}
        try:
            return json.loads(metadata_payload)
        except Exception:
            return {}

    def _serialize_metadata(self, payload: dict[str, Any]) -> str:
        try:
            return json.dumps(payload)
        except Exception:
            return "{}"

    def _normalize_optional_amount(self, amount: Decimal | float | int | None) -> Decimal | None:
        if amount is None:
            return None
        normalized = self._coerce_decimal(amount)
        if normalized <= Decimal("0"):
            raise ValidationException("Partial settlement amount must be greater than zero.")
        return normalized

    def _coerce_decimal(self, value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value)
            except Exception:
                return Decimal("0")
        return Decimal("0")

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise


__all__ = ["GiftCardSettlementService"]
