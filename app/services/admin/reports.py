from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.ledger_service import LedgerService
from app.services.provider.health import ProviderHealthService
from app.utils.exceptions import ValidationException


class ReportService:
    """Service for generating and retrieving reports."""

    def __init__(
        self,
        *,
        audit_repository: AuditLogRepository | None = None,
        ledger_service: LedgerService | None = None,
        transaction_repository: TransactionRepository | None = None,
        provider_repository: ProviderRepository | None = None,
        provider_health_service: ProviderHealthService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.audit_repository = audit_repository
        self.ledger_service = ledger_service
        self.transaction_repository = transaction_repository
        self.provider_repository = provider_repository
        self.provider_health_service = provider_health_service
        self.logger = logger or logging.getLogger(__name__)

    async def generate_report(self, **payload: Any) -> dict[str, Any]:
        report_type = str(payload.get("report_type") or "").strip().lower()

        if report_type == "ledger_summary":
            if self.ledger_service is None:
                raise ValidationException(detail="Ledger reporting is unavailable.", error_code="LEDGER_REPORTING_UNAVAILABLE")
            data = await self.ledger_service.get_statistics()
        elif report_type == "audit_log":
            if self.audit_repository is None:
                raise ValidationException(detail="Audit reporting is unavailable.", error_code="AUDIT_REPORTING_UNAVAILABLE")
            data = await self._generate_audit_log_report(payload)
        elif report_type == "transaction_summary":
            if self.transaction_repository is None:
                raise ValidationException(detail="Transaction reporting is unavailable.", error_code="TRANSACTION_REPORTING_UNAVAILABLE")
            data = await self._generate_transaction_summary_report(payload)
        elif report_type == "provider_health":
            data = await self._generate_provider_health_report()
        else:
            raise ValidationException(detail="Unsupported report type.", error_code="REPORT_TYPE_UNSUPPORTED")

        return {"success": True, "data": data, "meta": {"source": "admin.reports", "report_type": report_type}}

    async def _generate_audit_log_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        start_date = self._parse_date(payload.get("start_date"))
        end_date = self._parse_date(payload.get("end_date"))
        items, total = await self.audit_repository.search_audit_logs(
            action=payload.get("action"),
            category=payload.get("category"),
            resource=payload.get("resource"),
            start_date=start_date,
            end_date=end_date,
            page=1,
            page_size=100,
        )
        return {
            "total": total,
            "items": [self._serialize_audit_log(item) for item in items],
            "filters": {"start_date": payload.get("start_date"), "end_date": payload.get("end_date")},
        }

    async def _generate_transaction_summary_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        status = self._optional_text(payload.get("status"))
        category = self._optional_text(payload.get("category"))
        transaction_type = self._optional_text(payload.get("transaction_type"))
        reference = self._optional_text(payload.get("reference"))
        user_id = self._optional_text(payload.get("user_id"))

        items, total = await self.transaction_repository.get_admin_transactions(
            page=1,
            page_size=100,
            status=status,
            category=category,
            transaction_type=transaction_type,
            reference=reference,
            user_id=user_id,
        )
        return {
            "total": total,
            "items": [self._serialize_transaction(item) for item in items],
            "filters": {"status": status, "category": category, "transaction_type": transaction_type, "reference": reference, "user_id": user_id},
        }

    async def _generate_provider_health_report(self) -> dict[str, Any]:
        providers, total = await self.provider_repository.get_all_providers(page=1, page_size=1000)
        items: list[dict[str, Any]] = []
        for provider in providers:
            health_score = None
            if self.provider_health_service is not None:
                health = await self.provider_health_service.calculate_health_score(provider_id=provider.id)
                health_score = health.get("health_score")
            items.append(
                {
                    "id": str(provider.id),
                    "name": provider.name,
                    "category": provider.category,
                    "status": provider.status,
                    "health_status": provider.health_status,
                    "health_score": health_score,
                    "last_checked_at": provider.last_checked_at.isoformat() if provider.last_checked_at else None,
                }
            )
        return {"total": total, "providers": items}

    def _serialize_audit_log(self, audit_log: Any) -> dict[str, Any]:
        return {
            "id": str(getattr(audit_log, "id", "")),
            "actor_type": getattr(audit_log, "actor_type", None),
            "actor_id": getattr(audit_log, "actor_id", None),
            "action": getattr(audit_log, "action", None),
            "category": getattr(audit_log, "category", None),
            "resource_type": getattr(audit_log, "resource_type", None),
            "resource_id": getattr(audit_log, "resource_id", None),
            "description": getattr(audit_log, "description", None),
            "metadata_payload": self._parse_json(getattr(audit_log, "metadata_payload", None)),
            "created_at": getattr(audit_log, "created_at", None).isoformat() if getattr(audit_log, "created_at", None) else None,
        }

    def _serialize_transaction(self, transaction: Any) -> dict[str, Any]:
        return {
            "id": str(getattr(transaction, "id", "")),
            "reference": getattr(transaction, "reference", None),
            "user_id": str(getattr(transaction, "user_id", None)) if getattr(transaction, "user_id", None) else None,
            "wallet_id": str(getattr(transaction, "wallet_id", None)) if getattr(transaction, "wallet_id", None) else None,
            "transaction_type": getattr(transaction, "transaction_type", None),
            "category": getattr(transaction, "category", None),
            "amount": str(getattr(transaction, "amount", 0)),
            "currency": getattr(transaction, "currency", None),
            "status": getattr(transaction, "status", None),
            "created_at": getattr(transaction, "created_at", None).isoformat() if getattr(transaction, "created_at", None) else None,
        }

    def _parse_date(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValidationException(detail="Invalid date format.", error_code="INVALID_REPORT_DATE") from exc

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        value_str = str(value).strip()
        return value_str or None

    def _parse_json(self, raw_value: str | None) -> dict[str, Any]:
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"value": raw_value}
