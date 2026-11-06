from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from datetime import timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.schemas.admin_schema import DashboardResponse
from app.services.admin.dashboard import DashboardService
from app.utils.response import success_response


_LAGOS = timezone(timedelta(hours=1), name="Africa/Lagos")


@pytest_asyncio.fixture
async def dashboard_service():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield DashboardService(
            user_repository=UserRepository(session),
            transaction_repository=TransactionRepository(session),
            wallet_repository=WalletRepository(session),
            kyc_repository=object(),
        ), session
    await engine.dispose()


def _at(day: int, hour: int = 0, minute: int = 0, *, month: int = 1) -> datetime:
    return datetime(2025, month, day, hour, minute, tzinfo=_LAGOS)


def _user(*, email: str, created_at: datetime, last_login_at: datetime | None = None) -> User:
    return User(email=email, created_at=created_at, last_login_at=last_login_at)


def _transaction(
    *,
    user_id,
    reference: str,
    amount: str,
    currency: str,
    status: str,
    category: str = "airtime",
    provider_name: str | None = "provider-a",
    created_at: datetime,
) -> Transaction:
    return Transaction(
        reference=reference,
        user_id=user_id,
        transaction_type="purchase",
        category=category,
        amount=Decimal(amount),
        currency=currency,
        total_amount=Decimal(amount),
        status=status,
        provider_name=provider_name,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_dashboard_aggregates_and_lagos_day_comparison(dashboard_service):
    service, session = dashboard_service
    current_user = _user(
        email="current@example.test",
        created_at=_at(8),
        last_login_at=_at(8, 11),
    )
    previous_user = _user(
        email="previous@example.test",
        created_at=_at(7, 11, 59),
        last_login_at=_at(7, 15),
    )
    boundary_user = _user(
        email="boundary@example.test",
        created_at=_at(8, 13),
        last_login_at=_at(8, 13),
    )
    session.add_all([current_user, previous_user, boundary_user])
    await session.flush()

    session.add_all(
        [
            Wallet(
                user_id=current_user.id,
                currency="NGN",
                ledger_balance=Decimal("100.25"),
                available_balance=Decimal("80.00"),
                locked_balance=Decimal("20.25"),
            ),
            Wallet(
                user_id=previous_user.id,
                currency="NGN",
                ledger_balance=Decimal("50.00"),
                available_balance=Decimal("40.00"),
                locked_balance=Decimal("10.00"),
                is_active=False,
                status="frozen",
            ),
            Wallet(
                user_id=boundary_user.id,
                currency="USD",
                ledger_balance=Decimal("12.34"),
                available_balance=Decimal("12.34"),
                locked_balance=Decimal("0.00"),
            ),
        ]
    )
    session.add_all(
        [
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-completed",
                amount="10.25",
                currency="NGN",
                status="completed",
                created_at=_at(8, 1),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-pending",
                amount="4.00",
                currency="NGN",
                status="pending",
                created_at=_at(8, 2),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-failed",
                amount="8.00",
                currency="EUR",
                status="failed",
                category="data",
                provider_name=None,
                created_at=_at(8, 3),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-posted",
                amount="5.00",
                currency="NGN",
                status="posted",
                created_at=_at(8, 4),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-succeeded",
                amount="3.00",
                currency="USD",
                status="succeeded",
                created_at=_at(8, 5),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-settled",
                amount="1.00",
                currency="EUR",
                status="settled",
                created_at=_at(8, 6),
            ),
            _transaction(
                user_id=current_user.id,
                reference="dashboard-current-successful-normalized",
                amount="2.00",
                currency="NGN",
                status="SUCCESSFUL",
                created_at=_at(8, 7),
            ),
            _transaction(
                user_id=previous_user.id,
                reference="dashboard-previous-completed",
                amount="2.50",
                currency="USD",
                status="completed",
                created_at=_at(7, 23),
            ),
            _transaction(
                user_id=boundary_user.id,
                reference="dashboard-next-day-completed",
                amount="99.00",
                currency="NGN",
                status="completed",
                created_at=_at(9),
            ),
            _transaction(
                user_id=boundary_user.id,
                reference="dashboard-future-today-completed",
                amount="6.00",
                currency="NGN",
                status="completed",
                created_at=_at(8, 13),
            ),
            _transaction(
                user_id=boundary_user.id,
                reference="dashboard-reversed",
                amount="7.00",
                currency="NGN",
                status="reversed",
                created_at=_at(8, 4),
            ),
            _transaction(
                user_id=boundary_user.id,
                reference="dashboard-negative-completed",
                amount="-9.00",
                currency="NGN",
                status="completed",
                category="invalid-negative",
                provider_name="provider-invalid",
                created_at=_at(7, 22),
            ),
        ]
    )
    await session.flush()

    result = await service.get_stats(now=_at(8, 12))

    metrics = result["metrics"]
    assert metrics["total_users"] == 3
    assert metrics["active_users_today"] == 1
    assert metrics["new_registrations_today"] == 1
    assert metrics["total_transactions"] == 12
    assert metrics["pending_transactions"] == 1
    assert metrics["failed_transactions"] == 1

    comparison = metrics["period_comparison"]
    assert comparison["new_registrations_today"]["current"] == 1
    assert comparison["new_registrations_today"]["previous"] == 1
    assert comparison["new_registrations_today"]["change"] == 0
    assert comparison["new_registrations_today"]["current_period"] == {
        "start": "2025-01-08T00:00:00+01:00",
        "end": "2025-01-08T12:00:00+01:00",
    }
    assert comparison["new_registrations_today"]["previous_period"] == {
        "start": "2025-01-07T00:00:00+01:00",
        "end": "2025-01-07T12:00:00+01:00",
    }
    assert comparison["active_users_today"]["previous"] is None
    assert "most recent login" in comparison["active_users_today"]["unavailable_reason"]
    assert comparison["pending_transactions"]["previous"] is None
    assert comparison["pending_transactions"]["change"] is None
    assert comparison["total_users"]["previous"] is None

    assert result["wallet_balances"] == [
        {
            "currency": "NGN",
            "ledger_balance": "150.25",
            "available_balance": "120.00",
            "locked_balance": "30.25",
        },
        {
            "currency": "USD",
            "ledger_balance": "12.34",
            "available_balance": "12.34",
            "locked_balance": "0.00",
        },
    ]
    assert result["transaction_volume"] == [
        {"currency": "EUR", "amount": "1.00"},
        {"currency": "NGN", "amount": "122.25"},
        {"currency": "USD", "amount": "5.50"},
    ]
    assert result["financial_metrics"]["revenue"] is None
    assert result["financial_metrics"]["profit"] is None
    assert result["financial_metrics"]["revenue_unavailable_reason"]
    assert result["financial_metrics"]["profit_unavailable_reason"]

    categories = {
        (row["category"], row["provider"], row["currency"]): row
        for row in result["category_breakdown"]
    }
    assert categories[("airtime", "provider-a", "NGN")]["transaction_count"] == 7
    assert categories[("airtime", "provider-a", "NGN")]["transaction_volume"] == "122.25"
    assert categories[("invalid-negative", "provider-invalid", "NGN")]["transaction_volume"] == "0.00"
    assert categories[("data", None, "EUR")]["transaction_volume"] == "0.00"
    assert categories[("airtime", "provider-a", "EUR")]["transaction_volume"] == "1.00"

    daily = result["analytics"]["daily"]
    assert len(daily) == 7
    today_bucket = next(bucket for bucket in daily if bucket["bucket"] == "2025-01-08")
    assert today_bucket["registrations"] == 1
    assert today_bucket["transactions"] == 8
    assert today_bucket["transaction_volume"] == [
        {"currency": "EUR", "amount": "1.00"},
        {"currency": "NGN", "amount": "17.25"},
        {"currency": "USD", "amount": "3.00"},
    ]
    weekly = result["analytics"]["weekly"]
    monthly = result["analytics"]["monthly"]
    assert len(weekly) == 12
    assert len(monthly) == 12
    assert weekly[-1]["transactions"] == 10
    assert monthly[-1]["transactions"] == 10
    assert monthly[-1]["bucket"] == "2025-01"


@pytest.mark.asyncio
async def test_empty_dashboard_returns_supported_zeroes_and_valid_envelope(dashboard_service):
    service, _ = dashboard_service
    result = await service.get_stats(now=_at(8, 12))

    assert result["metrics"]["total_users"] == 0
    assert result["metrics"]["active_users_today"] == 0
    assert result["metrics"]["new_registrations_today"] == 0
    assert result["metrics"]["total_transactions"] == 0
    assert result["metrics"]["pending_transactions"] == 0
    assert result["metrics"]["failed_transactions"] == 0
    registration_comparison = result["metrics"]["period_comparison"]["new_registrations_today"]
    assert registration_comparison["previous"] == 0
    assert registration_comparison["change"] == 0
    assert result["metrics"]["period_comparison"]["active_users_today"]["previous"] is None
    assert result["wallet_balances"] == []
    assert result["transaction_volume"] == []
    assert result["category_breakdown"] == []
    assert result["analytics"] == {"daily": [], "weekly": [], "monthly": []}
    assert all(
        bucket["registrations"] == 0
        and bucket["transactions"] == 0
        and bucket["transaction_volume"] == []
        for cadence in result["analytics"].values()
        for bucket in cadence
    )

    envelope = success_response(data=result, message="Dashboard data retrieved successfully.")
    validated = DashboardResponse.model_validate(envelope)
    assert validated.success is True
    assert validated.status_code == 200
    assert validated.data.financial_metrics.revenue is None
    assert set(validated.data.metrics.period_comparison) == {
        "new_registrations_today",
        "active_users_today",
        "total_users",
        "total_transactions",
        "pending_transactions",
        "failed_transactions",
    }
