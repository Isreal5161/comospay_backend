from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.repositories.kyc_repository import KYCRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.ledger_service import LedgerService


_LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")
_ZERO = Decimal("0.00")


class DashboardService:
    """Build accurate admin dashboard aggregates from retained database records."""

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        transaction_repository: TransactionRepository,
        kyc_repository: KYCRepository,
        wallet_repository: WalletRepository,
        ledger_service: LedgerService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.transaction_repository = transaction_repository
        self.kyc_repository = kyc_repository
        self.wallet_repository = wallet_repository
        self.ledger_service = ledger_service
        self.logger = logger or logging.getLogger(__name__)

    async def get_stats(self, *, now: datetime | None = None, **payload: Any) -> dict[str, Any]:
        """Return dashboard data only; the controller supplies the API envelope."""
        lagos_now = self._as_lagos(now or datetime.now(_LAGOS))
        today = lagos_now.date()
        current_day = self._window(today, today + timedelta(days=1))
        current_day = (current_day[0], min(current_day[1], lagos_now))
        previous_day_start = self._window(today - timedelta(days=1), today)[0]
        previous_day = (
            previous_day_start,
            previous_day_start + (lagos_now - current_day[0]),
        )

        user_counts = await self.user_repository.get_dashboard_counts(
            current_day_start=current_day[0],
            current_day_end=current_day[1],
            previous_day_start=previous_day[0],
            previous_day_end=previous_day[1],
        )
        transaction_counts = await self.transaction_repository.get_dashboard_counts()
        wallet_rows = await self.wallet_repository.get_dashboard_balances_by_currency()
        total_volume_rows = await self.transaction_repository.get_successful_volume_by_currency()
        category_rows = await self.transaction_repository.get_dashboard_category_breakdown()

        analytics: dict[str, list[dict[str, Any]]] = {}
        for cadence, buckets in self._analytics_windows(today, lagos_now).items():
            analytics[cadence] = await self._build_analytics_buckets(buckets)

        current_registrations = user_counts["new_registrations_today"]
        previous_registrations = user_counts["new_registrations_previous_day"]
        comparison_windows = {
            "current_period": self._serialize_window(current_day),
            "previous_period": self._serialize_window(previous_day),
        }
        metrics = {
            **transaction_counts,
            "total_users": user_counts["total_users"],
            "active_users_today": user_counts["active_users_today"],
            "new_registrations_today": current_registrations,
            "period_comparison": {
                "new_registrations_today": {
                    "current": current_registrations,
                    "previous": previous_registrations,
                    "change": current_registrations - previous_registrations,
                    **comparison_windows,
                    "unavailable_reason": None,
                },
                "active_users_today": self._unavailable_comparison(
                    user_counts["active_users_today"],
                    **comparison_windows,
                    reason=(
                        "User.last_login_at stores only the most recent login, "
                        "so previous-day active users cannot be reconstructed."
                    ),
                ),
                "total_users": self._unavailable_comparison(
                    user_counts["total_users"],
                    reason=(
                        "User records have no historical snapshots and may be deleted, "
                        "so a prior-day cumulative total cannot be reconstructed."
                    ),
                ),
                "total_transactions": self._unavailable_comparison(
                    transaction_counts["total_transactions"],
                    reason=(
                        "Transaction records have no historical snapshots and may be deleted, "
                        "so a prior-day cumulative total cannot be reconstructed."
                    ),
                ),
                "pending_transactions": self._unavailable_comparison(
                    transaction_counts["pending_transactions"],
                    **comparison_windows,
                    reason=(
                        "Transaction.status stores only the current lifecycle state; "
                        "prior-day pending counts are not retained."
                    ),
                ),
                "failed_transactions": self._unavailable_comparison(
                    transaction_counts["failed_transactions"],
                    **comparison_windows,
                    reason=(
                        "Transaction.status stores only the current lifecycle state; "
                        "prior-day failed counts are not retained."
                    ),
                ),
            },
        }

        revenue_reason = (
            "Ledger classifications such as fees or commissions are recorded as customer "
            "wallet movements, not dedicated platform revenue accounts or settled income; "
            "Transaction.charges alone does not prove recognized revenue."
        )
        profit_reason = (
            "No retained accounting records identify both recognized revenue and its "
            "associated costs or expenses, so profit cannot be calculated."
        )

        return {
            "metrics": metrics,
            "wallet_balances": [
                {
                    "currency": currency,
                    "ledger_balance": self._money(ledger_balance),
                    "available_balance": self._money(available_balance),
                    "locked_balance": self._money(locked_balance),
                }
                for currency, ledger_balance, available_balance, locked_balance in wallet_rows
            ],
            "transaction_volume": [
                {"currency": currency, "amount": self._money(amount)}
                for currency, amount in total_volume_rows
            ],
            "financial_metrics": {
                "revenue": None,
                "profit": None,
                "revenue_unavailable_reason": revenue_reason,
                "profit_unavailable_reason": profit_reason,
            },
            "analytics": analytics,
            "category_breakdown": [
                {
                    "category": category,
                    "provider": provider,
                    "currency": currency,
                    "transaction_count": count,
                    "transaction_volume": self._money(volume),
                }
                for category, provider, currency, count, volume in category_rows
            ],
        }

    async def _build_analytics_buckets(
        self,
        buckets: list[tuple[str, datetime, datetime]],
    ) -> list[dict[str, Any]]:
        registration_counts = await self.user_repository.get_registration_counts_by_bucket(buckets)
        transaction_rows = await self.transaction_repository.get_dashboard_analytics_by_bucket(buckets)

        transactions_by_bucket: dict[str, int] = {}
        volume_by_bucket: dict[str, dict[str, Decimal]] = {}
        for bucket, currency, count, amount in transaction_rows:
            transactions_by_bucket[bucket] = transactions_by_bucket.get(bucket, 0) + count
            volume_by_bucket.setdefault(bucket, {})[currency] = amount

        if not any(registration_counts.values()) and not any(transactions_by_bucket.values()):
            return []

        return [
            {
                "bucket": label,
                "registrations": registration_counts.get(label, 0),
                "transactions": transactions_by_bucket.get(label, 0),
                "transaction_volume": [
                    {"currency": currency, "amount": self._money(amount)}
                    for currency, amount in sorted(volume_by_bucket.get(label, {}).items())
                ],
            }
            for label, _, _ in buckets
        ]

    @staticmethod
    def _analytics_windows(
        today: date,
        now: datetime,
    ) -> dict[str, list[tuple[str, datetime, datetime]]]:
        daily_start = today - timedelta(days=6)
        daily = [
            DashboardService._labeled_window(
                (daily_start + timedelta(days=offset)).isoformat(),
                daily_start + timedelta(days=offset),
                daily_start + timedelta(days=offset + 1),
            )
            for offset in range(7)
        ]

        current_week_start = today - timedelta(days=today.weekday())
        first_week_start = current_week_start - timedelta(weeks=11)
        weekly = [
            DashboardService._labeled_window(
                (first_week_start + timedelta(weeks=offset)).isoformat(),
                first_week_start + timedelta(weeks=offset),
                first_week_start + timedelta(weeks=offset + 1),
            )
            for offset in range(12)
        ]

        current_month_start = today.replace(day=1)
        first_month_start = DashboardService._shift_month(current_month_start, -11)
        monthly: list[tuple[str, datetime, datetime]] = []
        for offset in range(12):
            month_start = DashboardService._shift_month(first_month_start, offset)
            next_month_start = DashboardService._shift_month(month_start, 1)
            monthly.append(
                DashboardService._labeled_window(
                    month_start.strftime("%Y-%m"),
                    month_start,
                    next_month_start,
                )
            )
        return {
            cadence: [
                (label, start, min(end, now)) if index == len(buckets) - 1 else (label, start, end)
                for index, (label, start, end) in enumerate(buckets)
            ]
            for cadence, buckets in {"daily": daily, "weekly": weekly, "monthly": monthly}.items()
        }

    @staticmethod
    def _labeled_window(
        label: str,
        start_day: date,
        end_day: date,
    ) -> tuple[str, datetime, datetime]:
        return (
            label,
            datetime.combine(start_day, time.min, tzinfo=_LAGOS),
            datetime.combine(end_day, time.min, tzinfo=_LAGOS),
        )

    @staticmethod
    def _window(start_day: date, end_day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(start_day, time.min, tzinfo=_LAGOS),
            datetime.combine(end_day, time.min, tzinfo=_LAGOS),
        )

    @staticmethod
    def _shift_month(value: date, months: int) -> date:
        month_index = value.year * 12 + value.month - 1 + months
        year, zero_based_month = divmod(month_index, 12)
        return date(year, zero_based_month + 1, 1)

    @staticmethod
    def _as_lagos(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=_LAGOS)
        return value.astimezone(_LAGOS)

    @staticmethod
    def _serialize_window(window: tuple[datetime, datetime]) -> dict[str, str]:
        return {"start": window[0].isoformat(), "end": window[1].isoformat()}

    @staticmethod
    def _unavailable_comparison(
        current: int,
        *,
        reason: str,
        current_period: dict[str, str] | None = None,
        previous_period: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "current": current,
            "previous": None,
            "change": None,
            "current_period": current_period,
            "previous_period": previous_period,
            "unavailable_reason": reason,
        }

    @staticmethod
    def _money(value: Decimal | int | float | None) -> str:
        amount = Decimal(str(value if value is not None else "0.00"))
        return format(amount.quantize(Decimal("0.01")), "f")
