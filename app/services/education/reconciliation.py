from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import ValidationException


class EducationReconciliationService:
    """Reconcile education transactions with provider state and maintain a structured reconciliation trail."""

    CACHE_KEY_TEMPLATE = "education:reconciliation:{reference}"
    SETTINGS_PREFIX = "education_reconciliation"
    PROVIDER_SETTING_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    DEFAULT_SETTING_TEMPLATE = "{prefix}:{key}"

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        settings_repository: SystemSettingsRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        batch_size: int = 100,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.settings_repository = settings_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.batch_size = max(1, batch_size)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile a single education transaction with provider state and update internal records."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Transaction reference was not found.")

        self.logger.info("education_reconciliation_started", extra={"reference": reference})
        if transaction.status in {"completed", "succeeded", "settled"} and provider_operation is None:
            return await self._build_response(transaction, matched=True)

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for education reconciliation.")

        provider_response = await self._query_provider(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )

        reconciliation = self._compare_status(transaction=transaction, provider_response=provider_response)
        transaction.provider_name = provider_name or transaction.provider_name or provider_response.get("provider")
        transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
        transaction.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction.provider_transaction_id
        transaction.external_reference = transaction.provider_reference
        transaction.status = reconciliation["resolved_status"]
        transaction.metadata_payload = self._update_reconciliation_metadata(
            transaction.metadata_payload,
            {
                "event": "education_reconciled",
                "provider_status": provider_response.get("status"),
                "resolved_status": transaction.status,
                "discrepancy": reconciliation.get("discrepancy"),
                "reconciled_at": self._now_iso(),
                "provider_name": transaction.provider_name,
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
            "education_reconciliation_completed",
            extra={
                "reference": reference,
                "status": transaction.status,
                "discrepancy": reconciliation.get("discrepancy"),
            },
        )
        return await self._build_response(transaction, discrepancy=reconciliation.get("discrepancy"))

    async def reconcile_pending_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile all pending education transactions in paginated batches."""
        return await self._reconcile_batch(status="pending", provider_operation=provider_operation, provider_name=provider_name, page_size=page_size)

    async def reconcile_failed_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile all failed education transactions and detect provider state changes."""
        return await self._reconcile_batch(status="failed", provider_operation=provider_operation, provider_name=provider_name, page_size=page_size)

    async def retry_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Retry reconciliation by querying the provider again for the given reference."""
        self._validate_reference(reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for reconciliation retry.")

        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Transaction reference was not found.")

        transaction.status = "pending"
        transaction.metadata_payload = self._update_reconciliation_metadata(
            transaction.metadata_payload,
            {"event": "education_reconciliation_retry_scheduled", "provider_name": provider_name, "scheduled_at": self._now_iso()},
        )
        await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
        self.logger.info("education_reconciliation_retry_scheduled", extra={"reference": reference})
        return await self.reconcile_transaction(reference=reference, provider_operation=provider_operation, provider_name=provider_name)

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Detect inconsistencies between internal education transaction state and provider state."""
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for discrepancy detection.")

        provider_response = await self._query_provider(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )

        provider_status = self._normalize_status(provider_response.get("status"))
        internal_status = self._normalize_status(transaction.status)

        if internal_status != provider_status:
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
        """Generate a reconciliation report for education transactions."""
        page_size = page_size or self.batch_size
        report: dict[str, Any] = {
            "generated_at": self._now_iso(),
            "pending_count": 0,
            "failed_count": 0,
            "succeeded_count": 0,
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
        self.logger.info("education_reconciliation_report_generated", extra={"report_size": len(report["summary"])})
        return report

    async def _reconcile_batch(
        self,
        status: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        page_size = page_size or self.batch_size
        results: list[dict[str, Any]] = []
        while True:
            transactions, _ = await self.transaction_repository.get_transactions_by_status(status=status, page=page, page_size=page_size)
            if not transactions:
                break
            for transaction in transactions:
                try:
                    results.append(await self.reconcile_transaction(reference=transaction.reference, provider_operation=provider_operation, provider_name=provider_name))
                except Exception as exc:
                    self.logger.warning("education_reconciliation_batch_error", extra={"reference": transaction.reference, "error": str(exc)})
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
        settings = await self._load_provider_settings(provider_name=provider_name)
        if self.redis_client is not None and settings.get("cache_enabled", True):
            cached = await self._get_cached_provider_response(cache_key)
            if cached is not None:
                self.logger.info("education_reconciliation_provider_cache_hit", extra={"reference": transaction.reference})
                return cached

        provider_response = await self.provider_service.execute_education(
            operation=provider_operation,
            validate=self._validate_provider_payload,
            normalize=self._normalize_provider_response,
            payload={
                "reference": transaction.reference,
                "provider_name": provider_name,
                "transaction": {
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "provider_reference": transaction.provider_reference,
                },
            },
        )

        if self.redis_client is not None and settings.get("cache_enabled", True):
            await self._cache_provider_response(cache_key, provider_response, settings)

        return provider_response

    def _compare_status(self, *, transaction: Transaction, provider_response: dict[str, Any]) -> dict[str, Any]:
        provider_status = self._normalize_status(provider_response.get("status"))
        internal_status = self._normalize_status(transaction.status)
        discrepancy: dict[str, Any] = {"type": "matched", "message": "Statuses match.", "provider_status": provider_status}
        resolved_status = internal_status

        if provider_status != internal_status:
            discrepancy = {
                "type": "status_mismatch",
                "message": f"Internal status '{internal_status}' differs from provider status '{provider_status}'.",
                "provider_status": provider_status,
                "internal_status": internal_status,
            }
            resolved_status = provider_status

            if provider_status == "duplicate":
                resolved_status = "duplicate"
                discrepancy["type"] = "duplicate_callback"
                discrepancy["message"] = "Duplicate provider callback detected."
            elif provider_status == "timeout":
                resolved_status = "timeout"
                discrepancy["type"] = "timed_out"
                discrepancy["message"] = "Provider reported a timed-out transaction."
            elif provider_status == "reversed":
                resolved_status = "reversed"
                discrepancy["type"] = "reversed"
                discrepancy["message"] = "Provider reported the transaction as reversed."
        return {"resolved_status": resolved_status, "discrepancy": discrepancy}

    async def _get_cached_provider_response(self, cache_key: str) -> dict[str, Any] | None:
        try:
            raw = await self.redis_client.get(cache_key)
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception as exc:
            self.logger.warning("education_reconciliation_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_provider_response(self, cache_key: str, response: dict[str, Any], settings: dict[str, Any]) -> None:
        try:
            await self.redis_client.set(cache_key, json.dumps(response), ex=settings.get("cache_ttl_seconds", self.cache_ttl_seconds))
            self.logger.info("education_reconciliation_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("education_reconciliation_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def _load_provider_settings(self, *, provider_name: str | None) -> dict[str, Any]:
        provider_name_normalized = self._normalize_provider_name(provider_name)
        return {
            "cache_enabled": await self._load_bool_setting("cache_enabled", provider_name=provider_name_normalized, default=True),
            "cache_ttl_seconds": await self._load_int_setting("cache_ttl_seconds", provider_name=provider_name_normalized, default=self.cache_ttl_seconds),
        }

    async def _load_setting(self, key: str, provider_name: str | None = None) -> Any:
        if provider_name is not None:
            provider_key = self.PROVIDER_SETTING_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, provider=provider_name, key=key)
            record = await self.settings_repository.get_by_key(provider_key)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)

        default_key = self.DEFAULT_SETTING_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, key=key)
        record = await self.settings_repository.get_by_key(default_key)
        if record is not None and record.value is not None:
            return self._parse_setting_value(record.value, record.value_type)
        return None

    async def _load_bool_setting(self, key: str, provider_name: str | None = None, default: bool = False) -> bool:
        value = await self._load_setting(key, provider_name=provider_name)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        return default

    async def _load_int_setting(self, key: str, provider_name: str | None = None, default: int = 0) -> int:
        value = await self._load_setting(key, provider_name=provider_name)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return default

    def _parse_setting_value(self, value: str, value_type: str | None) -> Any:
        normalized_type = (value_type or "").strip().lower()
        if normalized_type == "json":
            try:
                return json.loads(value)
            except Exception:
                return None
        if normalized_type == "boolean":
            return str(value).strip().lower() in {"true", "1", "yes"}
        if normalized_type in {"int", "integer"}:
            try:
                return int(value)
            except Exception:
                return None
        if normalized_type in {"float", "decimal", "number", "numeric"}:
            try:
                from decimal import Decimal

                return Decimal(str(value))
            except Exception:
                return None
        return value

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Transaction reference is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": self._normalize_status(payload.get("status")),
            "provider": getattr(provider, "name", None) or self._coerce_string(payload.get("provider")),
            "provider_reference": self._coerce_string(payload.get("provider_reference") or payload.get("reference")),
            "provider_transaction_id": self._coerce_string(payload.get("provider_transaction_id") or payload.get("transaction_id")),
            "message": self._coerce_string(payload.get("message")),
        }

    def _normalize_status(self, status: Any) -> str:
        if status is None:
            return "pending"
        normalized = str(status).strip().lower()
        mapping = {
            "success": "succeeded",
            "successful": "succeeded",
            "succeeded": "succeeded",
            "complete": "completed",
            "completed": "completed",
            "settled": "settled",
            "failed": "failed",
            "failure": "failed",
            "error": "failed",
            "cancelled": "failed",
            "reversed": "reversed",
            "duplicate": "duplicate",
            "timeout": "timeout",
            "timed_out": "timeout",
            "pending": "pending",
            "processing": "pending",
            "in-progress": "pending",
        }
        return mapping.get(normalized, normalized)

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

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _normalize_provider_name(self, provider_name: str | None) -> str | None:
        if provider_name is None:
            return None
        normalized = provider_name.strip().lower()
        return normalized or None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["EducationReconciliationService"]
