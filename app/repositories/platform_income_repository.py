from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, or_, select

from app.models.platform_income import PlatformIncome
from app.models.platform_income_reversal import PlatformIncomeReversal
from app.models.provider_acquisition_cost import ProviderCostSnapshot
from app.models.transaction import Transaction


class PlatformIncomeRepository:
    """Persist attributable service-margin evidence and its revenue reversals."""

    _SUCCESS_STATUSES = {"completed", "posted", "successful", "succeeded", "settled"}
    _SUPPORTED_SERVICE_TYPES = {
        "airtime": {"airtime_purchase"},
        "data": {"data_purchase"},
        "electricity": {"electricity_purchase"},
        "tv": {"tv_purchase"},
        "education": {"education_purchase"},
    }
    _SERVICE_TRANSACTION_TYPES = {
        **_SUPPORTED_SERVICE_TYPES,
        "giftcard": {"giftcard_buy", "giftcard_sell"},
    }

    def __init__(self, session: Any) -> None:
        self.session = session

    async def record_verified_service_margin(
        self,
        *,
        transaction_id: UUID,
        provider_cost: Decimal,
        provider_cost_currency: str,
        provider_cost_reference: str,
        service_category: str,
        recognized_at: datetime | None = None,
    ) -> PlatformIncome:
        provider_cost = Decimal(provider_cost)
        provider_cost_currency = provider_cost_currency.strip().upper()
        provider_cost_reference = provider_cost_reference.strip()
        service_category = service_category.strip().lower()
        if not provider_cost.is_finite() or provider_cost < 0:
            raise ValueError("Actual provider cost must be a non-negative finite amount.")
        if not provider_cost_currency or len(provider_cost_currency) > 10:
            raise ValueError("Actual provider cost requires a valid currency.")
        if not provider_cost_reference:
            raise ValueError("Actual provider cost requires transaction-specific evidence.")
        if service_category not in self._SUPPORTED_SERVICE_TYPES:
            raise ValueError("Service margin accounting is unsupported for this service category.")

        transaction_result = await self.session.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .with_for_update()
        )
        transaction = transaction_result.scalar_one_or_none()
        if transaction is None:
            raise ValueError("Platform income requires an existing transaction.")
        if str(transaction.status).strip().lower() not in self._SUCCESS_STATUSES:
            raise ValueError("Platform income can only be recognized for a successful transaction.")
        currency = transaction.currency.strip().upper()
        if provider_cost_currency != currency:
            raise ValueError("Provider cost currency must match the source transaction currency.")
        transaction_type = str(transaction.transaction_type).strip().lower()
        if transaction_type not in self._SUPPORTED_SERVICE_TYPES[service_category]:
            raise ValueError("Service category does not match the source transaction type.")
        provider_identifier = str(transaction.provider_name or "").strip()
        provider_reference = str(
            transaction.provider_reference or transaction.provider_transaction_id or ""
        ).strip()
        if not provider_identifier or not provider_reference:
            raise ValueError("A provider and transaction-specific provider reference are required.")
        selling_amount = Decimal(transaction.amount)
        if not selling_amount.is_finite() or selling_amount <= 0:
            raise ValueError("The customer selling price must be positive and finite.")
        gross_margin = selling_amount - provider_cost
        amount = max(gross_margin, Decimal("0.00"))
        status = "recognized" if gross_margin > 0 else "loss" if gross_margin < 0 else "zero_margin"
        source_type = "provider_actual_cost"
        income_category = service_category

        result = await self.session.execute(
            select(PlatformIncome)
            .where(
                PlatformIncome.transaction_id == transaction_id,
                PlatformIncome.source_type == source_type,
                PlatformIncome.income_category == income_category,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is not None:
            if (
                record.amount != amount
                or record.currency != currency
                or record.source_reference != provider_cost_reference
                or record.provider_identifier != provider_identifier
                or record.provider_reference != provider_reference
                or record.selling_amount != selling_amount
                or record.provider_cost != provider_cost
                or record.gross_margin != gross_margin
            ):
                raise ValueError("A conflicting service-margin record already exists for this transaction.")
            return record

        now = datetime.now(timezone.utc)
        record = PlatformIncome(
            transaction_id=transaction_id,
            source_type=source_type,
            source_reference=provider_cost_reference,
            income_category=income_category,
            provider_identifier=provider_identifier,
            provider_reference=provider_reference,
            selling_amount=selling_amount,
            provider_cost=provider_cost,
            gross_margin=gross_margin,
            currency=currency,
            amount=amount,
            status=status,
            recognized_at=recognized_at or now,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def record_reversal(
        self,
        *,
        platform_income_id: UUID,
        amount: Decimal,
        currency: str,
        event_reference: str,
        source_reference: str | None = None,
    ) -> PlatformIncomeReversal:
        amount = Decimal(amount)
        currency = currency.strip().upper()
        event_reference = event_reference.strip()
        source_reference = source_reference.strip() if source_reference else None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("A platform-income reversal must be a positive finite amount.")
        if not currency or len(currency) > 10 or not event_reference:
            raise ValueError("A platform-income reversal requires a valid currency and event reference.")

        income_result = await self.session.execute(
            select(PlatformIncome)
            .where(PlatformIncome.id == platform_income_id)
            .with_for_update()
        )
        income = income_result.scalar_one_or_none()
        if income is None:
            raise ValueError("The recognized platform-income record was not found.")

        event_result = await self.session.execute(
            select(PlatformIncomeReversal)
            .where(PlatformIncomeReversal.event_reference == event_reference)
            .with_for_update()
        )
        existing_event = event_result.scalar_one_or_none()
        if existing_event is not None:
            if (
                existing_event.platform_income_id != platform_income_id
                or existing_event.amount != amount
                or existing_event.currency != currency
                or existing_event.source_reference != source_reference
            ):
                raise ValueError("A conflicting platform-income reversal event already exists.")
            return existing_event

        if income.status != "recognized":
            raise ValueError("Only recognized platform income can be reversed.")
        if income.currency != currency:
            raise ValueError("Reversal currency must match the recognized income currency.")
        if amount > income.amount - income.reversal_amount:
            raise ValueError("A platform-income reversal cannot exceed the remaining recognized amount.")

        now = datetime.now(timezone.utc)
        event = PlatformIncomeReversal(
            platform_income_id=platform_income_id,
            event_reference=event_reference,
            source_reference=source_reference,
            currency=currency,
            amount=amount,
            created_at=now,
        )
        income.reversal_amount += amount
        income.reversal_reference = event_reference
        income.updated_at = now
        self.session.add_all((income, event))
        await self.session.flush()
        return event

    async def get_totals_by_currency(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service_category: str | None = None,
        provider_identifier: str | None = None,
        currency: str | None = None,
    ) -> dict[str, dict[str, Decimal]]:
        filters = self._income_filters(
            start_date=start_date,
            end_date=end_date,
            service_category=service_category,
            provider_identifier=provider_identifier,
            currency=currency,
        )

        result = await self.session.execute(
            select(
                PlatformIncome.currency,
                func.sum(
                    case(
                        (PlatformIncome.status == "recognized", PlatformIncome.amount),
                        else_=Decimal("0.00"),
                    )
                ).label("gross"),
                func.sum(
                    case(
                        (PlatformIncome.status == "recognized", PlatformIncome.reversal_amount),
                        else_=Decimal("0.00"),
                    )
                ).label("reversed"),
                func.sum(PlatformIncome.selling_amount).label("selling"),
                func.sum(PlatformIncome.provider_cost).label("provider_cost"),
                func.sum(PlatformIncome.gross_margin).label("gross_service_margin"),
                func.sum(
                    case(
                        (PlatformIncome.status == "recognized", 1),
                        else_=0,
                    )
                ).label("recognized_count"),
            )
            .where(PlatformIncome.status.in_(("recognized", "loss", "zero_margin")), *filters)
            .group_by(PlatformIncome.currency)
            .order_by(PlatformIncome.currency)
        )
        totals: dict[str, dict[str, Decimal]] = {}
        for currency, gross_value, reversed_value, selling_value, provider_cost_value, margin_value, recognized_count in result.all():
            gross = gross_value or Decimal("0.00")
            reversed_amount = reversed_value or Decimal("0.00")
            totals[currency] = {
                "gross_amount": gross,
                "reversal_amount": reversed_amount,
                "net_amount": gross - reversed_amount,
                "selling_amount": selling_value or Decimal("0.00"),
                "provider_cost": provider_cost_value or Decimal("0.00"),
                "gross_service_margin": margin_value or Decimal("0.00"),
                "loss_amount": Decimal("0.00"),
                "recognized_count": int(recognized_count or 0),
            }
        losses = await self.session.execute(
            select(
                PlatformIncome.currency,
                func.sum(-PlatformIncome.gross_margin).label("loss"),
            )
            .where(
                PlatformIncome.status == "loss",
                *filters,
            )
            .group_by(PlatformIncome.currency)
        )
        for currency, loss_value in losses.all():
            totals.setdefault(
                currency,
                {
                    "gross_amount": Decimal("0.00"),
                    "reversal_amount": Decimal("0.00"),
                    "net_amount": Decimal("0.00"),
                    "selling_amount": Decimal("0.00"),
                    "provider_cost": Decimal("0.00"),
                    "gross_service_margin": Decimal("0.00"),
                    "loss_amount": Decimal("0.00"),
                    "recognized_count": 0,
                },
            )["loss_amount"] = loss_value or Decimal("0.00")
        for values in totals.values():
            values.setdefault("loss_amount", Decimal("0.00"))
        return totals

    async def get_service_margin_breakdown(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service_category: str | None = None,
        provider_identifier: str | None = None,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = self._income_filters(
            start_date=start_date,
            end_date=end_date,
            service_category=service_category,
            provider_identifier=provider_identifier,
            currency=currency,
        )
        result = await self.session.execute(
            select(
                PlatformIncome.income_category,
                PlatformIncome.currency,
                func.sum(PlatformIncome.selling_amount),
                func.sum(PlatformIncome.provider_cost),
                func.sum(PlatformIncome.gross_margin),
                func.sum(PlatformIncome.amount),
                func.sum(PlatformIncome.reversal_amount),
                func.sum(
                    case(
                        (PlatformIncome.status == "loss", -PlatformIncome.gross_margin),
                        else_=Decimal("0.00"),
                    )
                ),
                func.count(PlatformIncome.id),
            )
            .where(PlatformIncome.status.in_(("recognized", "loss", "zero_margin")), *filters)
            .group_by(PlatformIncome.income_category, PlatformIncome.currency)
            .order_by(PlatformIncome.income_category, PlatformIncome.currency)
        )
        return [
            {
                "service_category": category,
                "currency": currency,
                "selling_amount": selling or Decimal("0.00"),
                "provider_cost": cost or Decimal("0.00"),
                "gross_margin": margin or Decimal("0.00"),
                "recognized_margin": recognized or Decimal("0.00"),
                "reversal_amount": reversed_amount or Decimal("0.00"),
                "net_margin": (recognized or Decimal("0.00")) - (reversed_amount or Decimal("0.00")),
                "loss_amount": loss or Decimal("0.00"),
                "transaction_count": int(count or 0),
            }
            for category, currency, selling, cost, margin, recognized, reversed_amount, loss, count in result.all()
        ]

    async def get_service_margin_trends(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service_category: str | None = None,
        provider_identifier: str | None = None,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        filters = self._income_filters(
            start_date=start_date,
            end_date=end_date,
            service_category=service_category,
            provider_identifier=provider_identifier,
            currency=currency,
        )
        period = func.date(PlatformIncome.recognized_at)
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            period = func.date(func.timezone("UTC", PlatformIncome.recognized_at))
        result = await self.session.execute(
            select(
                period.label("period"),
                PlatformIncome.currency,
                func.sum(PlatformIncome.amount),
                func.sum(PlatformIncome.reversal_amount),
            )
            .where(PlatformIncome.status == "recognized", *filters)
            .group_by(period, PlatformIncome.currency)
            .order_by(period, PlatformIncome.currency)
        )
        return [
            {
                "date": str(period_value),
                "currency": currency,
                "gross_margin": gross or Decimal("0.00"),
                "reversal_amount": reversed_amount or Decimal("0.00"),
                "net_margin": (gross or Decimal("0.00")) - (reversed_amount or Decimal("0.00")),
            }
            for period_value, currency, gross, reversed_amount in result.all()
        ]

    @staticmethod
    def _income_filters(
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        service_category: str | None,
        provider_identifier: str | None,
        currency: str | None,
    ) -> list[Any]:
        filters: list[Any] = []
        if start_date is not None:
            filters.append(PlatformIncome.recognized_at >= start_date)
        if end_date is not None:
            filters.append(PlatformIncome.recognized_at <= end_date)
        if service_category:
            filters.append(PlatformIncome.income_category == service_category.strip().lower())
        if provider_identifier:
            filters.append(func.lower(PlatformIncome.provider_identifier) == provider_identifier.strip().lower())
        if currency:
            filters.append(PlatformIncome.currency == currency.strip().upper())
        return filters

    async def get_cost_evidence_gaps(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service_category: str | None = None,
        provider_identifier: str | None = None,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[Any] = [
            func.lower(Transaction.status).in_(self._SUCCESS_STATUSES),
            func.lower(Transaction.transaction_type).in_(
                tuple(
                    transaction_type
                    for transaction_types in self._SERVICE_TRANSACTION_TYPES.values()
                    for transaction_type in transaction_types
                )
            ),
            or_(
                ProviderCostSnapshot.id.is_(None),
                ProviderCostSnapshot.source_class.in_(("UNAVAILABLE", "ADMIN_CONFIGURED")),
            ),
            ~select(PlatformIncome.id).where(
                PlatformIncome.transaction_id == Transaction.id,
                PlatformIncome.source_type == "provider_actual_cost",
                PlatformIncome.status.in_(("recognized", "loss", "zero_margin")),
            ).exists(),
        ]
        if start_date is not None:
            filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            filters.append(Transaction.created_at <= end_date)
        if service_category:
            if service_category.strip().lower() == "data":
                filters.append(func.lower(Transaction.category) == "data")
            else:
                filters.append(func.lower(Transaction.category) == service_category.strip().lower())
        if provider_identifier:
            filters.append(func.lower(Transaction.provider_name) == provider_identifier.strip().lower())
        if currency:
            filters.append(func.upper(Transaction.currency) == currency.strip().upper())

        result = await self.session.execute(
            select(
                Transaction.category,
                Transaction.currency,
                Transaction.provider_name,
                func.count(Transaction.id),
            )
            .outerjoin(ProviderCostSnapshot, ProviderCostSnapshot.transaction_id == Transaction.id)
            .where(*filters)
            .group_by(Transaction.category, Transaction.currency, Transaction.provider_name)
            .order_by(Transaction.category, Transaction.currency, Transaction.provider_name)
        )
        return [
            {
                "service_category": category,
                "currency": currency,
                "provider_identifier": provider_name or "Unspecified",
                "transaction_count": int(count or 0),
                "reason": "No verified transaction-specific provider acquisition-cost evidence.",
            }
            for category, currency, provider_name, count in result.all()
        ]


    async def get_reversals_for_income(self, income_id: UUID) -> list[PlatformIncomeReversal]:
        result = await self.session.execute(
            select(PlatformIncomeReversal)
            .where(PlatformIncomeReversal.platform_income_id == income_id)
            .order_by(PlatformIncomeReversal.created_at, PlatformIncomeReversal.id)
        )
        return list(result.scalars().all())

    async def get_for_transaction(self, transaction_id: UUID) -> list[PlatformIncome]:
        result = await self.session.execute(
            select(PlatformIncome)
            .where(PlatformIncome.transaction_id == transaction_id)
            .order_by(PlatformIncome.currency, PlatformIncome.source_type, PlatformIncome.income_category)
        )
        return list(result.scalars().all())
