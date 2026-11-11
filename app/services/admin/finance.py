from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.repositories.platform_income_repository import PlatformIncomeRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.provider_acquisition_cost_repository import ProviderAcquisitionCostRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.ledger_service import LedgerService
from app.utils.exceptions import AppException


class FinanceService:
    """Service for finance and revenue administration views."""

    def __init__(
        self,
        *,
        ledger_service: LedgerService | None = None,
        transaction_repository: TransactionRepository | None = None,
        provider_repository: ProviderRepository | None = None,
        platform_income_repository: PlatformIncomeRepository | None = None,
        provider_cost_repository: ProviderAcquisitionCostRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ledger_service = ledger_service
        self.transaction_repository = transaction_repository
        self.provider_repository = provider_repository
        self.platform_income_repository = platform_income_repository
        self.provider_cost_repository = provider_cost_repository
        self.logger = logger or logging.getLogger(__name__)

    async def get_platform_statistics(self, **payload: Any) -> dict[str, Any]:
        ledger_statistics = {}
        if self.ledger_service is not None:
            ledger_statistics = await self.ledger_service.get_statistics()

        transaction_count = 0
        if self.transaction_repository is not None:
            _, transaction_count = await self.transaction_repository.get_admin_transactions(page=1, page_size=1)

        provider_count = 0
        active_provider_count = 0
        if self.provider_repository is not None:
            providers, provider_count = await self.provider_repository.get_all_providers(page=1, page_size=1000)
            active_provider_count = sum(1 for provider in providers if getattr(provider, "is_active", False))

        return {
            "success": True,
            "data": {
                "ledger_statistics": ledger_statistics,
                "transaction_count": transaction_count,
                "provider_count": provider_count,
                "active_provider_count": active_provider_count,
            },
            "meta": {"source": "admin.finance"},
        }

    async def _get_revenue_by_currency(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service_category: str | None = None,
        provider: str | None = None,
        currency: str | None = None,
    ) -> dict[str, dict[str, Decimal]]:
        if self.platform_income_repository is not None:
            return await self.platform_income_repository.get_totals_by_currency(
                start_date=start_date,
                end_date=end_date,
                service_category=service_category,
                provider_identifier=provider,
                currency=currency,
            )
        return {}

    async def get_finance_report(self, **payload: Any) -> dict[str, Any]:
        if self.transaction_repository is None:
            raise RuntimeError("Finance reporting requires the transaction repository.")

        start_date: datetime | None = payload.get("start_date")
        end_date: datetime | None = payload.get("end_date")
        category = payload.get("category")
        provider = payload.get("provider")
        currency_filter = payload.get("currency")
        service_type = payload.get("service_type") or category
        aggregates = await self.transaction_repository.get_finance_aggregates(
            start_date=start_date,
            end_date=end_date,
            category=category,
            provider=provider,
            currency=currency_filter,
        )

        revenue_totals = await self._get_revenue_by_currency(
            start_date=start_date,
            end_date=end_date,
            service_category=service_type,
            provider=provider,
            currency=currency_filter,
        )
        recognized_revenue_totals = {
            currency: values
            for currency, values in revenue_totals.items()
            if values["recognized_count"] > 0
        }
        revenue_available = bool(recognized_revenue_totals)
        single_revenue_total = (
            next(iter(recognized_revenue_totals.values()))
            if len(recognized_revenue_totals) == 1
            else None
        )
        if revenue_available:
            revenue_reason = (
                "Revenue includes only persisted retained-income records with a verified source. "
                "Transaction charges without ownership evidence are excluded."
            )
        elif revenue_totals:
            revenue_reason = (
                "Verified provider-cost records are available, but none establish positive retained revenue. "
                "Transaction charges and wallet movements are not treated as revenue."
            )
        else:
            revenue_reason = (
                "No verified retained platform-income records are available. "
                "Transaction charges and wallet movements are not treated as revenue."
            )

        currencies: dict[str, dict[str, Any]] = {}
        categories: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        trends: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        providers: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for row in aggregates["transactions"]:
            currency = str(row["currency"]).upper()
            category = str(row["category"])
            amount = Decimal(row["amount"])
            summary = currencies.setdefault(
                currency,
                {
                    "currency": currency,
                    "gross_transaction_volume": Decimal("0"),
                    "wallet_funding_volume": Decimal("0"),
                    "transfer_volume": Decimal("0"),
                    "withdrawal_volume": Decimal("0"),
                    "other_volume": Decimal("0"),
                    "successful_transaction_count": 0,
                    "pending_transaction_count": 0,
                    "failed_transaction_count": 0,
                    "other_transaction_count": 0,
                    "unverified_customer_charges": Decimal("0"),
                    "refund_adjustments": Decimal("0"),
                    "reversal_adjustments": Decimal("0"),
                },
            )
            summary["gross_transaction_volume"] += amount
            summary["unverified_customer_charges"] += Decimal(row["charges"])
            summary["successful_transaction_count"] += row["count"]
            normalized_category = f"{category} {row['transaction_type']}".strip().lower()
            if "fund" in normalized_category or "deposit" in normalized_category:
                summary["wallet_funding_volume"] += amount
            elif "transfer" in normalized_category:
                summary["transfer_volume"] += amount
            elif "withdraw" in normalized_category or "payout" in normalized_category:
                summary["withdrawal_volume"] += amount
            else:
                summary["other_volume"] += amount
            categories[(currency, category)] += amount
            trends[(str(row["period"]), currency)] += amount
            providers[(currency, str(row["provider_name"] or "Unspecified"))] += amount

        for row in aggregates["adjustments"]:
            currency = str(row["currency"]).upper()
            summary = currencies.setdefault(
                currency,
                {
                    "currency": currency,
                    "gross_transaction_volume": Decimal("0"),
                    "wallet_funding_volume": Decimal("0"),
                    "transfer_volume": Decimal("0"),
                    "withdrawal_volume": Decimal("0"),
                    "other_volume": Decimal("0"),
                    "successful_transaction_count": 0,
                    "pending_transaction_count": 0,
                    "failed_transaction_count": 0,
                    "other_transaction_count": 0,
                    "unverified_customer_charges": Decimal("0"),
                    "refund_adjustments": Decimal("0"),
                    "reversal_adjustments": Decimal("0"),
                },
            )
            entry_type = str(row["entry_type"]).lower()
            transaction_type = str(row["transaction_type"]).lower()
            if entry_type == "refund" or "refund" in transaction_type:
                summary["refund_adjustments"] += Decimal(row["credits"])
            elif entry_type == "reversal" or "reversal" in transaction_type:
                summary["reversal_adjustments"] += Decimal(row["debits"])

        for row in aggregates["transaction_status_counts"]:
            currency = str(row["currency"]).upper()
            status = str(row["status"]).lower()
            summary = currencies.setdefault(
                currency,
                {
                    "currency": currency,
                    "gross_transaction_volume": Decimal("0"),
                    "wallet_funding_volume": Decimal("0"),
                    "transfer_volume": Decimal("0"),
                    "withdrawal_volume": Decimal("0"),
                    "other_volume": Decimal("0"),
                    "successful_transaction_count": 0,
                    "pending_transaction_count": 0,
                    "failed_transaction_count": 0,
                    "other_transaction_count": 0,
                    "unverified_customer_charges": Decimal("0"),
                    "refund_adjustments": Decimal("0"),
                    "reversal_adjustments": Decimal("0"),
                },
            )
            count = int(row["count"])
            if status in {"completed", "posted", "successful", "succeeded", "settled"}:
                continue
            elif status in {"pending", "processing"}:
                summary["pending_transaction_count"] += count
            elif status in {"failed", "cancelled", "canceled"}:
                summary["failed_transaction_count"] += count
            else:
                summary["other_transaction_count"] += count

        service_margin_breakdown: list[dict[str, Any]] = []
        service_margin_trends: list[dict[str, Any]] = []
        cost_evidence_gaps: list[dict[str, Any]] = []
        if self.platform_income_repository is not None:
            service_margin_breakdown = await self.platform_income_repository.get_service_margin_breakdown(
                start_date=start_date,
                end_date=end_date,
                service_category=service_type,
                provider_identifier=provider,
                currency=currency_filter,
            )
            service_margin_trends = await self.platform_income_repository.get_service_margin_trends(
                start_date=start_date,
                end_date=end_date,
                service_category=service_type,
                provider_identifier=provider,
                currency=currency_filter,
            )
            cost_evidence_gaps = await self.platform_income_repository.get_cost_evidence_gaps(
                start_date=start_date,
                end_date=end_date,
                service_category=service_type,
                provider_identifier=provider,
                currency=currency_filter,
            )

        provider_cost_breakdown: list[dict[str, Any]] = []
        awaiting_cost_count = 0
        if self.provider_cost_repository is not None:
            provider_cost_breakdown = await self.provider_cost_repository.finance_breakdown(
                start_date=start_date,
                end_date=end_date,
                provider_identifier=provider,
                service_type=service_type,
                currency=currency_filter,
                source_class=payload.get("source_class"),
            )
            _, awaiting_cost_count = await self.provider_cost_repository.list_awaiting_transactions(
                page=1,
                page_size=1,
                service_type=service_type,
                currency=currency_filter,
                provider_identifier=provider,
                start_date=start_date,
                end_date=end_date,
            )

        estimated_by_currency: dict[str, dict[str, Decimal | int]] = {}
        for row in provider_cost_breakdown:
            if row["source_class"] != "ADMIN_CONFIGURED" or row["provider_cost"] is None or row["margin"] is None:
                continue
            estimate = estimated_by_currency.setdefault(
                row["currency"],
                {
                    "selling_amount": Decimal("0.00"),
                    "provider_cost": Decimal("0.00"),
                    "estimated_margin": Decimal("0.00"),
                    "transaction_count": 0,
                },
            )
            estimate["selling_amount"] += row["selling_amount"]
            estimate["provider_cost"] += row["provider_cost"]
            estimate["estimated_margin"] += row["margin"]
            estimate["transaction_count"] += row["transaction_count"]

        service_transaction_types = {
            "airtime_purchase",
            "data_purchase",
            "electricity_purchase",
            "tv_purchase",
            "education_purchase",
            "giftcard_buy",
        }
        successful_service_sales: dict[str, dict[str, Decimal | int]] = {}
        for row in aggregates["transactions"]:
            if str(row["transaction_type"]).strip().lower() not in service_transaction_types:
                continue
            sales = successful_service_sales.setdefault(
                str(row["currency"]).upper(),
                {"selling_amount": Decimal("0.00"), "transaction_count": 0},
            )
            sales["selling_amount"] += Decimal(row["amount"])
            sales["transaction_count"] += row["count"]

        def money(value: Decimal) -> str:
            return str(value.quantize(Decimal("0.01")))

        activity, total = await self.transaction_repository.get_finance_activity(
            page=payload.get("page", 1),
            page_size=payload.get("page_size", 20),
            search=payload.get("search"),
            category=payload.get("category"),
            status=payload.get("status"),
            provider=payload.get("provider"),
            currency=payload.get("currency"),
            start_date=start_date,
            end_date=end_date,
        )
        return {
            "success": True,
            "data": {
                "revenue": {
                    "available": revenue_available,
                    "amount": (
                        str(single_revenue_total["net_amount"].quantize(Decimal("0.01")))
                        if revenue_available and single_revenue_total is not None
                        else None
                    ),
                    "net_amount": (
                        str(single_revenue_total["net_amount"].quantize(Decimal("0.01")))
                        if revenue_available and single_revenue_total is not None
                        else None
                    ),
                    "currency": next(iter(recognized_revenue_totals)) if single_revenue_total is not None else None,
                    "reason": revenue_reason,
                },
                "revenue_by_currency": [
                    {
                        "currency": currency,
                        "gross_amount": str(values["gross_amount"].quantize(Decimal("0.01"))),
                        "reversal_amount": str(values["reversal_amount"].quantize(Decimal("0.01"))),
                        "net_amount": str(values["net_amount"].quantize(Decimal("0.01"))),
                        "selling_amount": str(values["selling_amount"].quantize(Decimal("0.01"))),
                        "provider_cost": str(values["provider_cost"].quantize(Decimal("0.01"))),
                        "gross_service_margin": str(values["gross_service_margin"].quantize(Decimal("0.01"))),
                        "loss_amount": str(values["loss_amount"].quantize(Decimal("0.01"))),
                        "recognized_count": values["recognized_count"],
                    }
                    for currency, values in sorted(recognized_revenue_totals.items())
                ],
                "service_margin_breakdown": [
                    {
                        "service_category": row["service_category"],
                        "currency": row["currency"],
                        "selling_amount": money(row["selling_amount"]),
                        "provider_cost": money(row["provider_cost"]),
                        "gross_margin": money(row["gross_margin"]),
                        "recognized_margin": money(row["recognized_margin"]),
                        "reversal_amount": money(row["reversal_amount"]),
                        "net_margin": money(row["net_margin"]),
                        "loss_amount": money(row["loss_amount"]),
                        "transaction_count": row["transaction_count"],
                    }
                    for row in service_margin_breakdown
                ],
                "estimated_service_margin_by_currency": [
                    {
                        "currency": curr,
                        "selling_amount": money(values["selling_amount"]),
                        "provider_cost": money(values["provider_cost"]),
                        "estimated_margin": money(values["estimated_margin"]),
                        "transaction_count": values["transaction_count"],
                    }
                    for curr, values in sorted(estimated_by_currency.items())
                ],
                "successful_service_sales_by_currency": [
                    {
                        "currency": curr,
                        "selling_amount": money(values["selling_amount"]),
                        "transaction_count": values["transaction_count"],
                    }
                    for curr, values in sorted(successful_service_sales.items())
                ],
                "service_margin_trends": [
                    {
                        "date": row["date"],
                        "currency": row["currency"],
                        "gross_margin": money(row["gross_margin"]),
                        "reversal_amount": money(row["reversal_amount"]),
                        "net_margin": money(row["net_margin"]),
                    }
                    for row in service_margin_trends
                ],
                "cost_evidence_unavailable": [
                    {
                        "service_category": row["service_category"],
                        "currency": row["currency"],
                        "provider_identifier": row["provider_identifier"],
                        "transaction_count": row["transaction_count"],
                        "reason": row["reason"],
                    }
                    for row in cost_evidence_gaps
                ],
                "provider_cost_breakdown": [
                    {
                        "service_type": row["service_type"],
                        "provider_identifier": row["provider_identifier"],
                        "currency": row["currency"],
                        "source_class": row["source_class"],
                        "selling_amount": money(row["selling_amount"]),
                        "provider_cost": money(row["provider_cost"]) if row["provider_cost"] is not None else None,
                        "margin": money(row["margin"]) if row["margin"] is not None else None,
                        "transaction_count": row["transaction_count"],
                    }
                    for row in provider_cost_breakdown
                ],
                "awaiting_cost_verification_count": awaiting_cost_count,
                "currencies": [
                    {
                        key: money(value) if isinstance(value, Decimal) else value
                        for key, value in summary.items()
                    }
                    for _, summary in sorted(currencies.items())
                ],
                "categories": [
                    {"currency": currency, "category": category, "transaction_volume": money(amount)}
                    for (currency, category), amount in sorted(categories.items())
                ],
                "available_categories": aggregates["available_categories"],
                "available_currencies": sorted({str(currency).upper() for currency in aggregates["available_currencies"]}),
                "providers": [
                    {"currency": currency, "provider": provider, "transaction_volume": money(amount)}
                    for (currency, provider), amount in sorted(providers.items())
                ],
                "trends": [
                    {"date": period, "currency": currency, "transaction_volume": money(amount)}
                    for (period, currency), amount in sorted(trends.items())
                ],
                "activity": {
                    "items": [self._finance_transaction_item(transaction) for transaction in activity],
                    "page": payload.get("page", 1),
                    "page_size": payload.get("page_size", 20),
                    "total": total,
                    "total_pages": (total + payload.get("page_size", 20) - 1) // payload.get("page_size", 20),
                },
            },
            "meta": {"source": "admin.finance"},
        }

    async def get_finance_transaction(self, *, transaction_id: UUID) -> dict[str, Any] | None:
        if self.transaction_repository is None:
            raise RuntimeError("Finance reporting requires the transaction repository.")
        transaction, ledger_entries = await self.transaction_repository.get_finance_transaction(transaction_id)
        if transaction is None:
            raise AppException(status_code=404, detail="Finance transaction was not found.")

        trusted_income = []
        if self.platform_income_repository is not None:
            trusted_income = await self.platform_income_repository.get_for_transaction(transaction.id)
        recognized_income = [record for record in trusted_income if str(record.status).lower() == "recognized"]
        cost_snapshot = (
            await self.provider_cost_repository.get_transaction_snapshot(transaction.id)
            if self.provider_cost_repository is not None
            else None
        )
        service_margin_evidence = []
        for record in trusted_income:
            reversals = []
            if self.platform_income_repository is not None:
                reversals = await self.platform_income_repository.get_reversals_for_income(record.id)
            service_margin_evidence.append({
                "service_category": record.income_category,
                "status": record.status,
                "cost_source": record.source_type,
                "provider_identifier": record.provider_identifier,
                "provider_reference": record.provider_reference,
                "provider_cost_reference": record.source_reference,
                "selling_amount": str(record.selling_amount.quantize(Decimal("0.01"))),
                "provider_cost": str(record.provider_cost.quantize(Decimal("0.01"))),
                "gross_margin": str(record.gross_margin.quantize(Decimal("0.01"))),
                "recognized_amount": str(record.amount.quantize(Decimal("0.01"))),
                "reversal_amount": str(record.reversal_amount.quantize(Decimal("0.01"))),
                "net_amount": str((record.amount - record.reversal_amount).quantize(Decimal("0.01"))),
                "currency": record.currency,
                "reversals": [
                    {
                        "event_reference": reversal.event_reference,
                        "source_reference": reversal.source_reference,
                        "amount": str(reversal.amount.quantize(Decimal("0.01"))),
                        "currency": reversal.currency,
                        "created_at": reversal.created_at.isoformat() if reversal.created_at else None,
                    }
                    for reversal in reversals
                ],
            })
        gross_revenue = sum((record.amount for record in recognized_income), Decimal("0.00"))
        reversed_revenue = sum((record.reversal_amount for record in recognized_income), Decimal("0.00"))
        net_revenue = gross_revenue - reversed_revenue
        revenue_currency = recognized_income[0].currency if recognized_income else transaction.currency

        return {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "category": transaction.category,
            "transaction_type": transaction.transaction_type,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "charges": str(transaction.charges),
            "total_amount": str(transaction.total_amount),
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
            "platform_revenue": {
                "available": bool(recognized_income),
                "amount": str(net_revenue.quantize(Decimal("0.01"))) if recognized_income else None,
                "gross_amount": str(gross_revenue.quantize(Decimal("0.01"))) if recognized_income else None,
                "reversal_amount": str(reversed_revenue.quantize(Decimal("0.01"))) if recognized_income else None,
                "net_amount": str(net_revenue.quantize(Decimal("0.01"))) if recognized_income else None,
                "currency": revenue_currency,
            },
            "service_margin_evidence": service_margin_evidence,
            "provisional_cost_snapshot": (
                {
                    "service_type": cost_snapshot.service_type,
                    "provider_identifier": cost_snapshot.provider_identifier,
                    "provider_reference": cost_snapshot.provider_reference,
                    "source_class": cost_snapshot.source_class,
                    "selling_amount": str(cost_snapshot.selling_amount.quantize(Decimal("0.01"))),
                    "provider_cost": (
                        str(cost_snapshot.cost_amount.quantize(Decimal("0.01")))
                        if cost_snapshot.cost_amount is not None
                        else None
                    ),
                    "estimated_margin": (
                        str(cost_snapshot.gross_margin.quantize(Decimal("0.01")))
                        if cost_snapshot.gross_margin is not None
                        else None
                    ),
                    "currency": cost_snapshot.currency,
                    "reason": cost_snapshot.reason,
                    "snapshot_at": cost_snapshot.cost_snapshot_at.isoformat()
                    if cost_snapshot.cost_snapshot_at
                    else None,
                }
                if cost_snapshot is not None
                else None
            ),
            "ledger_entries": [
                {
                    "transaction_reference": entry.transaction_reference,
                    "transaction_type": entry.transaction_type,
                    "entry_type": entry.entry_type,
                    "status": entry.status,
                    "currency": entry.currency,
                    "debit_amount": str(entry.debit_amount),
                    "credit_amount": str(entry.credit_amount),
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                }
                for entry in ledger_entries
            ],
        }

    @staticmethod
    def _finance_transaction_item(transaction: Any) -> dict[str, Any]:
        return {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "category": transaction.category,
            "transaction_type": transaction.transaction_type,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "charges": str(transaction.charges),
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
        }
