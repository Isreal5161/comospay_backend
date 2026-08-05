from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

from redis.asyncio import Redis

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.repositories.transaction_repository import TransactionRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import ValidationException


class GiftCardReconciliationService:
    """Reconcile gift card transactions against provider records and detect inconsistent states."""

    CACHE_KEY_TEMPLATE = "giftcard:reconciliation:{reference}"

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile a single gift card transaction with the provider and update internal records."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference.strip())
        if transaction is None:
            raise ValidationException("Gift card transaction reference was not found.")

        self.logger.info("giftcard_reconciliation_started", extra={"reference": reference, "provider_name": provider_name})

        if transaction.status in {"completed", "succeeded", "settled"} and provider_operation is None:
            return await self._build_response(transaction, reconciled=True)

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card reconciliation.")

        async with self._transaction_scope():
            provider_response = await self._query_provider(
                transaction=transaction,
                provider_operation=provider_operation,
                provider_name=provider_name,
            )

            resolution = self._resolve_status(transaction=transaction, provider_response=provider_response)
            transaction.status = resolution["resolved_status"]
            transaction.provider_name = provider_name or transaction.provider_name or provider_response.get("provider")
            transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction.provider_transaction_id
            transaction.external_reference = transaction.provider_reference or transaction.external_reference
            transaction.metadata_payload = self._update_reconciliation_metadata(
                transaction.metadata_payload,
                {
                    "event": "giftcard_reconciled",
                    "provider_status": provider_response.get("status"),
                    "resolved_status": transaction.status,
                    "discrepancy": resolution.get("discrepancy"),
                    "reconciled_at": self._now_iso(),
                },
            )

            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                external_reference=transaction.external_reference,
                metadata_payload=transaction.metadata_payload,
            )

        self.logger.info(
            "giftcard_reconciliation_completed",
            extra={"reference": reference, "status": transaction.status, "discrepancy": resolution.get("discrepancy")},
        )
        return await self._build_response(transaction, discrepancy=resolution.get("discrepancy"))

    async def reconcile_pending_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile all gift card transactions that remain pending."""
        return await self._reconcile_batch(status="pending", provider_operation=provider_operation, provider_name=provider_name, page_size=page_size)

    async def reconcile_failed_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile gift card transactions that have failed and may require recovery."""
        return await self._reconcile_batch(status="failed", provider_operation=provider_operation, provider_name=provider_name, page_size=page_size)

    async def retry_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Retry reconciliation for a gift card transaction."""
        self._validate_reference(reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required to retry gift card reconciliation.")
        return await self.reconcile_transaction(reference=reference, provider_operation=provider_operation, provider_name=provider_name)

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Detect inconsistent gift card transaction state compared with provider status."""
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required to detect discrepancies.")

        provider_response = await self._query_provider(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )

        provider_status = self._normalize_status(provider_response.get("status"))
        internal_status = self._normalize_status(transaction.status)

        if provider_status != internal_status:
            discrepancy: dict[str, Any] = {
                "reference": transaction.reference,
                "internal_status": internal_status,
                "provider_status": provider_status,
                "provider_reference": provider_response.get("provider_reference"),
            }
            if provider_status == "duplicate":
                discrepancy["type"] = "duplicate_callback"
                discrepancy["message"] = "Duplicate provider callback detected."
            elif provider_status == "timeout":
                discrepancy["type"] = "timed_out"
                discrepancy["message"] = "Provider reported a timed-out transaction."
            elif provider_status == "reversed":
                discrepancy["type"] = "reversed"
                discrepancy["message"] = "Provider reported the transaction as reversed."
            else:
                discrepancy["type"] = "status_mismatch"
                discrepancy["message"] = f"Internal status '{internal_status}' differs from provider status '{provider_status}'."
            return discrepancy

        return {"reference": transaction.reference, "status": "matched", "provider_status": provider_status}

    async def generate_reconciliation_report(self, *, page_size: int | None = None) -> dict[str, Any]:
        """Generate a reconciliation report for gift card transaction states."""
        page_size = page_size or 100
        report: dict[str, Any] = {
            "generated_at": self._now_iso(),
            "pending_count": 0,
            "failed_count": 0,
            "succeeded_count": 0,
            "completed_count": 0,
            "settled_count": 0,
            "reversed_count": 0,
            "duplicate_count": 0,
            "timeout_count": 0,
            "summary": [],
        }
        for status in ["pending", "failed", "succeeded", "completed", "settled", "reversed", "duplicate", "timeout"]:
            page = 1
            while True:
                transactions, _ = await self.transaction_repository.get_transactions_by_status(status=status, page=page, page_size=page_size)
                if not transactions:
                    break
                report[f"{status}_count"] = report.get(f"{status}_count", 0) + len(transactions)
                for transaction in transactions:
                    report["summary"].append(
                        {
                            "reference": transaction.reference,
                            "status": transaction.status,
                            "provider_name": transaction.provider_name,
                            "provider_reference": transaction.provider_reference,
                            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
                        }
                    )
                if len(transactions) < page_size:
                    break
                page += 1
        report["duplicate_count"] = sum(1 for item in report["summary"] if item["status"] == "duplicate")
        report["timeout_count"] = sum(1 for item in report["summary"] if item["status"] == "timeout")
        self.logger.info("giftcard_reconciliation_report_generated", extra={"summary_size": len(report["summary"])})
        return report

    async def _reconcile_batch(
        self,
        status: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        page_size = page_size or 100
        results: list[dict[str, Any]] = []
        while True:
            transactions, _ = await self.transaction_repository.get_transactions_by_status(status=status, page=page, page_size=page_size)
            if not transactions:
                break
            for transaction in transactions:
                try:
                    results.append(
                        await self.reconcile_transaction(
                            reference=transaction.reference,
                            provider_operation=provider_operation,
                            provider_name=provider_name,
                        )
                    )
                except Exception as exc:
                    self.logger.warning("giftcard_reconciliation_batch_error", extra={"reference": transaction.reference, "error": str(exc)})
            if len(transactions) < page_size:
                break
            page += 1
        return results

    async def _query_provider(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]],
        provider_name: str | None,
    ) -> dict[str, Any]:
        cache_key = self.CACHE_KEY_TEMPLATE.format(reference=transaction.reference)
        if self.redis_client is not None:
            cached = await self._get_cached_provider_response(cache_key)
            if cached is not None:
                self.logger.info("giftcard_reconciliation_cache_hit", extra={"reference": transaction.reference})
                return cached

        provider_response = await self.provider_service.execute_giftcard(
            operation=provider_operation,
            validate=self._validate_provider_payload,
            normalize=self._normalize_provider_response,
            payload={
                "reference": transaction.reference,
                "provider_name": provider_name,
                "amount": str(transaction.amount),
                "currency": transaction.currency,
                "provider_reference": transaction.provider_reference,
            },
        )

        if self.redis_client is not None:
            await self._cache_provider_response(cache_key, provider_response)

        return provider_response

    def _resolve_status(self, *, transaction: Transaction, provider_response: dict[str, Any]) -> dict[str, Any]:
        internal_status = self._normalize_status(transaction.status)
        provider_status = self._normalize_status(provider_response.get("status"))
        if internal_status == provider_status:
            return {"resolved_status": provider_status, "discrepancy": None}

        discrepancy: dict[str, Any] = {
            "type": "status_mismatch",
            "internal_status": internal_status,
            "provider_status": provider_status,
            "message": f"Internal status '{internal_status}' differs from provider status '{provider_status}'.",
        }
        if provider_status == "duplicate":
            discrepancy["type"] = "duplicate_callback"
            discrepancy["message"] = "Duplicate provider callback detected."
        elif provider_status == "timeout":
            discrepancy["type"] = "timed_out"
            discrepancy["message"] = "Provider reported a timed-out gift card transaction."
        elif provider_status == "reversed":
            discrepancy["type"] = "reversed"
            discrepancy["message"] = "Provider reported the gift card transaction as reversed."

        return {"resolved_status": provider_status, "discrepancy": discrepancy}

    async def _get_cached_provider_response(self, cache_key: str) -> dict[str, Any] | None:
        try:
            raw = await self.redis_client.get(cache_key)  # type: ignore[union-attr]
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            self.logger.warning("giftcard_reconciliation_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
        return None

    async def _cache_provider_response(self, cache_key: str, response: dict[str, Any]) -> None:
        try:
            await self.redis_client.set(cache_key, json.dumps(response), ex=self.cache_ttl_seconds)  # type: ignore[union-attr]
            self.logger.info("giftcard_reconciliation_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("giftcard_reconciliation_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    def _update_reconciliation_metadata(self, payload: str | None, entry: dict[str, Any]) -> str:
        metadata = self._parse_metadata(payload)
        events = metadata.get("reconciliation_events")
        if not isinstance(events, list):
            events = []
        events.append(entry)
        metadata["reconciliation_events"] = events
        return self._serialize_metadata(metadata)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except Exception:
            return {}

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str:
        if payload is None:
            return "{}"
        try:
            return json.dumps(payload)
        except Exception:
            return json.dumps({})

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Gift card transaction reference is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required for gift card reconciliation.")

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
            "message": self._coerce_string(payload.get("message") or payload.get("error")),
        }

    def _normalize_status(self, status: Any) -> str:
        if status is None:
            return "pending"
        normalized = str(status).strip().lower()
        if normalized in {"success", "successful", "succeeded"}:
            return "succeeded"
        if normalized in {"completed"}:
            return "completed"
        if normalized in {"settled"}:
            return "settled"
        if normalized in {"failed", "failure", "error"}:
            return "failed"
        if normalized in {"cancelled", "canceled"}:
            return "failed"
        if normalized in {"reversed"}:
            return "reversed"
        if normalized in {"duplicate"}:
            return "duplicate"
        if normalized in {"timeout", "timed_out", "time_out"}:
            return "timeout"
        if normalized in {"pending", "processing", "queued", "in_progress", "in-progress"}:
            return "pending"
        return normalized

    async def _build_response(self, transaction: Transaction, *, discrepancy: dict[str, Any] | None = None, reconciled: bool = False) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "reconciled": reconciled,
            "discrepancy": discrepancy,
            "metadata_payload": self._parse_metadata(transaction.metadata_payload),
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise


__all__ = ["GiftCardReconciliationService"]
