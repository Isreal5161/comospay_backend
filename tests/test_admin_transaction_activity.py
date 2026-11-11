from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.ledger import Ledger
from app.models.platform_income_reversal import PlatformIncomeReversal
from app.models.provider_acquisition_cost import ProviderCostSnapshot
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.platform_income_repository import PlatformIncomeRepository
from app.repositories.provider_acquisition_cost_repository import ProviderAcquisitionCostRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.admin.transactions import TransactionAdministrationService
from app.services.admin.payments import PaymentAdministrationService
from app.services.admin.finance import FinanceService
from app.services.admin.wallet import WalletAdministrationService
from app.controllers.admin_controller import AdminController
from app.middleware.admin_middleware import AdminMiddleware
from app.utils.exceptions import AuthorizationException, ValidationException

ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


def _set_verified_airtime_transaction(transaction: Transaction) -> None:
    transaction.transaction_type = "airtime_purchase"
    transaction.category = "airtime"
    transaction.provider_name = "VerifiedTestProvider"
    transaction.provider_reference = "PROVIDER-TEST-001"


class FakeAdminRepository:
    async def get_by_id(self, admin_id: UUID):
        if admin_id != ADMIN_ID:
            return None
        return type("Admin", (), {"is_active": True, "status": "active", "role": "admin"})()


@pytest_asyncio.fixture
async def transaction_services():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            email="maria@example.test",
            first_name="Maria",
            last_name="Okafor",
            status="active",
        )
        session.add(user)
        await session.flush()
        wallet = Wallet(user_id=user.id, currency="NGN")
        session.add(wallet)
        await session.flush()
        transaction = Transaction(
            id=UUID("33333333-3333-4333-8333-333333333333"),
            reference="CPAY-READ-001",
            user_id=user.id,
            wallet_id=wallet.id,
            transaction_type="wallet_funding",
            category="deposit",
            amount=Decimal("250.00"),
            currency="NGN",
            charges=Decimal("0.00"),
            total_amount=Decimal("250.00"),
            status="completed",
            provider_name="Flutterwave",
            provider_reference="FW-READ-001",
            external_reference="EXT-READ-001",
            metadata_payload=json.dumps({
                "channel": "bank_transfer",
                "api_token": "must-not-be-returned",
                "account_number": "1234567890",
                "account_number_last4": "7890",
            }),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session.add(transaction)
        newer_transaction = Transaction(
            id=UUID("44444444-4444-4444-8444-444444444444"),
            reference="CPAY-READ-002",
            user_id=user.id,
            wallet_id=wallet.id,
            transaction_type="airtime_purchase",
            category="airtime",
            amount=Decimal("50.00"),
            currency="NGN",
            charges=Decimal("0.00"),
            total_amount=Decimal("50.00"),
            status="failed",
            provider_name="TestProvider",
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        session.add(newer_transaction)
        session.add(
            Transaction(
                id=UUID("55555555-5555-4555-8555-555555555555"),
                reference="CPAY-READ-003",
                user_id=user.id,
                wallet_id=wallet.id,
                transaction_type="wallet_transfer_internal",
                category="transfer",
                amount=Decimal("30.00"),
                currency="NGN",
                charges=Decimal("0.00"),
                total_amount=Decimal("30.00"),
                status="pending",
                created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
        )
        await session.flush()
        session.add(
            Ledger(
                transaction_reference=transaction.reference,
                transaction_type="WALLET_FUNDING",
                entry_type="CREDIT",
                status="COMPLETED",
                currency="NGN",
                opening_balance=Decimal("0.00"),
                credit_amount=Decimal("250.00"),
                debit_amount=Decimal("0.00"),
                closing_balance=Decimal("250.00"),
                wallet_id=wallet.id,
                user_id=user.id,
                related_transaction_id=transaction.id,
                created_by="system",
            )
        )
        session.add(
            Ledger(
                transaction_reference="ADJ-READ-001",
                transaction_type="ADMIN_CREDIT",
                entry_type="CREDIT",
                status="COMPLETED",
                currency="NGN",
                opening_balance=Decimal("250.00"),
                credit_amount=Decimal("15.00"),
                debit_amount=Decimal("0.00"),
                closing_balance=Decimal("265.00"),
                wallet_id=wallet.id,
                user_id=user.id,
                created_by=f"admin:{ADMIN_ID}",
                metadata_payload={"reason": "Support adjustment", "admin_id": str(ADMIN_ID)},
            )
        )
        await session.flush()

        ledger_repository = LedgerRepository(session)
        transaction_service = TransactionAdministrationService(
            transaction_repository=TransactionRepository(session),
            ledger_repository=ledger_repository,
            admin_repository=FakeAdminRepository(),
        )
        wallet_service = WalletAdministrationService(
            wallet_repository=object(),
            ledger_repository=ledger_repository,
            admin_repository=FakeAdminRepository(),
        )
        yield transaction_service, wallet_service, transaction
    await engine.dispose()


@pytest.mark.asyncio
async def test_global_transaction_list_supports_owner_search_provider_and_counts(transaction_services):
    transaction_service, _, _ = transaction_services
    result = await transaction_service.list_transactions(
        admin_id=ADMIN_ID,
        page=1,
        page_size=20,
        search="maria",
        provider="flutterwave",
    )
    data = result["data"]

    assert data["total"] == 1
    assert data["items"][0]["reference"] == "CPAY-READ-001"
    assert data["items"][0]["user"] == {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "Maria Okafor",
        "email": "maria@example.test",
    }
    assert data["summary"] == {"total": 1, "successful": 1, "pending": 0, "failed": 0}


@pytest.mark.asyncio
async def test_global_transaction_list_supports_internal_id_search_date_filter_and_sort(transaction_services):
    transaction_service, _, transaction = transaction_services
    searched = await transaction_service.list_transactions(
        admin_id=ADMIN_ID,
        page=1,
        page_size=20,
        search=str(transaction.id),
    )
    assert [item["id"] for item in searched["data"]["items"]] == [str(transaction.id)]

    filtered = await transaction_service.list_transactions(
        admin_id=ADMIN_ID,
        page=1,
        page_size=1,
        category="airtime",
        status="failed",
        start_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
        end_date=datetime(2026, 2, 2, tzinfo=timezone.utc),
        order_by="created_at",
        descending=True,
    )
    assert filtered["data"]["total"] == 1
    assert filtered["data"]["items"][0]["reference"] == "CPAY-READ-002"


@pytest.mark.asyncio
async def test_transaction_details_allowlist_metadata_and_related_ledger(transaction_services):
    transaction_service, _, transaction = transaction_services
    result = await transaction_service.get_transaction_details(
        admin_id=ADMIN_ID,
        transaction_id=transaction.id,
    )
    data = result["data"]

    assert data["metadata"] == {
        "channel": "bank_transfer",
        "account_number_last4": "7890",
    }
    assert "api_token" not in data["metadata"]
    assert "account_number" not in data["metadata"]
    assert len(data["ledger_entries"]) == 1
    assert data["ledger_entries"][0]["source"] == "ledger"


@pytest.mark.asyncio
async def test_payment_list_uses_persisted_records_and_category_method_search(transaction_services):
    transaction_service, _, transaction = transaction_services
    payment_service = PaymentAdministrationService(transaction_service=transaction_service)

    second_page = await payment_service.list_payments(
        admin_id=ADMIN_ID,
        page=2,
        page_size=1,
    )
    assert second_page["data"]["total"] == 2
    assert second_page["data"]["total_pages"] == 2
    assert second_page["data"]["items"][0]["reference"] == "CPAY-READ-001"

    result = await payment_service.list_payments(
        admin_id=ADMIN_ID,
        page=1,
        page_size=20,
        payment_category="wallet_funding",
        method="bank_transfer",
        search="maria",
    )
    data = result["data"]

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == str(transaction.id)
    assert data["items"][0]["payment_type"] == "wallet_funding"
    assert data["items"][0]["method"] == "bank_transfer"
    assert data["summary"] == {"total": 1, "successful": 1, "pending": 0, "failed": 0}


@pytest.mark.asyncio
async def test_payment_details_include_real_ledger_and_only_masked_bank_data(transaction_services):
    transaction_service, _, transaction = transaction_services
    payment_service = PaymentAdministrationService(transaction_service=transaction_service)

    result = await payment_service.get_payment_details(
        admin_id=ADMIN_ID,
        transaction_id=transaction.id,
    )
    data = result["data"]

    assert data["payment_type"] == "wallet_funding"
    assert data["method"] == "bank_transfer"
    assert data["metadata"]["account_number_last4"] == "7890"
    assert "account_number" not in data["metadata"]
    assert "api_token" not in data["metadata"]
    assert len(data["ledger_entries"]) == 1


@pytest.mark.asyncio
async def test_payment_listing_requires_an_active_admin(transaction_services):
    transaction_service, _, _ = transaction_services
    payment_service = PaymentAdministrationService(transaction_service=transaction_service)

    with pytest.raises(AuthorizationException):
        await payment_service.list_payments(
            admin_id=UUID("99999999-9999-4999-8999-999999999999"),
            page=1,
            page_size=20,
        )


@pytest.mark.asyncio
async def test_manual_wallet_adjustments_remain_separate_ledger_records(transaction_services):
    _, wallet_service, _ = transaction_services
    result = await wallet_service.list_admin_adjustments(
        admin_id=ADMIN_ID,
        page=1,
        page_size=20,
        search="maria",
    )
    data = result["data"]

    assert data["total"] == 1
    assert data["items"][0]["source"] == "ledger_adjustment"
    assert data["items"][0]["transaction_reference"] == "ADJ-READ-001"
    assert data["items"][0]["user"]["email"] == "maria@example.test"


@pytest.mark.asyncio
async def test_global_transaction_list_requires_active_admin(transaction_services):
    transaction_service, _, _ = transaction_services
    with pytest.raises(AuthorizationException):
        await transaction_service.list_transactions(page=1, page_size=20)


@pytest.mark.asyncio
async def test_global_transaction_list_rejects_unsafe_page_size(transaction_services):
    transaction_service, _, _ = transaction_services
    with pytest.raises(ValidationException):
        await transaction_service.list_transactions(
            admin_id=ADMIN_ID,
            page=1,
            page_size=101,
        )


@pytest.mark.asyncio
async def test_finance_report_separates_volume_adjustments_and_currencies(transaction_services):
    transaction_service, _, transaction = transaction_services
    repository = transaction_service.transaction_repository
    session = repository.session
    user_id = transaction.user_id
    wallet_id = transaction.wallet_id
    session.add(
        Transaction(
            id=UUID("66666666-6666-4666-8666-666666666666"),
            reference="CPAY-READ-USD",
            user_id=user_id,
            wallet_id=wallet_id,
            transaction_type="wallet_transfer_internal",
            category="transfer",
            amount=Decimal("20.00"),
            currency="USD",
            charges=Decimal("2.00"),
            total_amount=Decimal("22.00"),
            status="settled",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    session.add(
        Transaction(
            id=UUID("77777777-7777-4777-8777-777777777777"),
            reference="CPAY-REFUND-TXN",
            user_id=user_id,
            wallet_id=wallet_id,
            transaction_type="PAYMENT_REFUND",
            category="refund",
            amount=Decimal("15.00"),
            currency="NGN",
            charges=Decimal("0.00"),
            total_amount=Decimal("15.00"),
            status="completed",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    session.add(
        Ledger(
            transaction_reference="CPAY-REFUND-001",
            transaction_type="PAYMENT_REFUND",
            entry_type="REFUND",
            status="COMPLETED",
            currency="NGN",
            opening_balance=Decimal("250.00"),
            credit_amount=Decimal("15.00"),
            debit_amount=Decimal("0.00"),
            closing_balance=Decimal("265.00"),
            wallet_id=wallet_id,
            user_id=user_id,
            related_transaction_id=transaction.id,
            created_by="system",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    session.add(
        Ledger(
            transaction_reference="CPAY-REVERSAL-001",
            transaction_type="WALLET_TRANSFER_REVERSAL",
            entry_type="REVERSAL",
            status="COMPLETED",
            currency="NGN",
            opening_balance=Decimal("265.00"),
            credit_amount=Decimal("0.00"),
            debit_amount=Decimal("5.00"),
            closing_balance=Decimal("260.00"),
            wallet_id=wallet_id,
            user_id=user_id,
            related_transaction_id=transaction.id,
            created_by="system",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    session.add(
        Ledger(
            transaction_reference="CPAY-REFUND-PENDING",
            transaction_type="PAYMENT_REFUND",
            entry_type="REFUND",
            status="PENDING",
            currency="NGN",
            opening_balance=Decimal("260.00"),
            credit_amount=Decimal("80.00"),
            debit_amount=Decimal("0.00"),
            closing_balance=Decimal("340.00"),
            wallet_id=wallet_id,
            user_id=user_id,
            related_transaction_id=transaction.id,
            created_by="system",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    )
    await session.flush()

    finance_service = FinanceService(transaction_repository=repository)
    result = await finance_service.get_finance_report(
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 2, 23, 59, tzinfo=timezone.utc),
        page=1,
        page_size=1,
    )
    data = result["data"]
    totals = {item["currency"]: item for item in data["currencies"]}

    assert data["revenue"]["available"] is False
    assert data["revenue"]["amount"] is None
    assert totals["NGN"]["gross_transaction_volume"] == "250.00"
    assert totals["NGN"]["wallet_funding_volume"] == "250.00"
    assert totals["NGN"]["refund_adjustments"] == "15.00"
    assert totals["NGN"]["reversal_adjustments"] == "5.00"
    assert totals["USD"]["gross_transaction_volume"] == "20.00"
    assert totals["USD"]["transfer_volume"] == "20.00"
    assert len(data["activity"]["items"]) == 1
    assert data["activity"]["total"] == 3
    assert all(point["currency"] in {"NGN", "USD"} for point in data["trends"])


@pytest.mark.asyncio
async def test_platform_income_repository_is_idempotent_for_repeat_processing(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    repository = PlatformIncomeRepository(session=session)
    _set_verified_airtime_transaction(transaction)

    first = await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("237.50"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-001",
        recognized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("237.50"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-001",
        recognized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert first.id == second.id
    assert first.selling_amount == Decimal("250.00")
    assert first.provider_cost == Decimal("237.50")
    assert first.gross_margin == Decimal("12.50")
    assert first.amount == Decimal("12.50")
    assert len(await repository.get_for_transaction(transaction.id)) == 1


@pytest.mark.asyncio
async def test_income_recognition_uses_callers_transaction_scope(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    repository = PlatformIncomeRepository(session=session)
    _set_verified_airtime_transaction(transaction)
    transaction.status = "pending"
    await session.flush()

    transaction_scope = await session.begin_nested()
    transaction.status = "completed"
    await session.flush()
    await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("247.00"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-atomic",
    )
    await transaction_scope.rollback()
    await session.refresh(transaction)

    assert transaction.status == "pending"
    assert await repository.get_for_transaction(transaction.id) == []


@pytest.mark.asyncio
async def test_finance_report_uses_persisted_retained_income_only(transaction_services):
    transaction_service, _, transaction = transaction_services
    repository = PlatformIncomeRepository(session=transaction_service.transaction_repository.session)
    _set_verified_airtime_transaction(transaction)
    await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("232.60"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-002",
        recognized_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=repository,
    )
    result = await finance_service.get_finance_report(
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 2, 23, 59, tzinfo=timezone.utc),
        page=1,
        page_size=20,
    )
    details = await finance_service.get_finance_transaction(transaction_id=transaction.id)

    assert result["data"]["revenue"]["available"] is True
    assert result["data"]["revenue"]["amount"] == "17.40"
    assert result["data"]["revenue_by_currency"] == [{
        "currency": "NGN",
        "gross_amount": "17.40",
        "provider_cost": "232.60",
        "selling_amount": "250.00",
        "gross_service_margin": "17.40",
        "loss_amount": "0.00",
        "reversal_amount": "0.00",
        "net_amount": "17.40",
        "recognized_count": 1,
    }]
    assert result["data"]["cost_evidence_unavailable"] == []
    assert details["platform_revenue"]["available"] is True
    assert details["platform_revenue"]["net_amount"] == "17.40"


@pytest.mark.asyncio
async def test_unverified_bank_transfer_charge_is_not_reported_as_revenue(transaction_services):
    transaction_service, _, transaction = transaction_services
    transaction.charges = Decimal("9.99")
    transaction.transaction_type = "wallet_transfer_bank"
    transaction.category = "transfer"
    transaction.status = "completed"
    await transaction_service.transaction_repository.session.flush()

    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=PlatformIncomeRepository(session=transaction_service.transaction_repository.session),
    )
    details = await finance_service.get_finance_transaction(transaction_id=transaction.id)
    report = await finance_service.get_finance_report(page=1, page_size=20)

    assert details["platform_revenue"]["available"] is False
    assert details["platform_revenue"]["net_amount"] is None
    assert report["data"]["revenue"]["available"] is False
    assert report["data"]["revenue"]["amount"] is None
    assert report["data"]["revenue_by_currency"] == []
    assert next(row for row in report["data"]["currencies"] if row["currency"] == "NGN")[
        "unverified_customer_charges"
    ] == "9.99"


@pytest.mark.asyncio
async def test_finance_report_lists_successful_service_transactions_missing_cost_evidence(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    _set_verified_airtime_transaction(transaction)
    transaction.status = "completed"
    session.add(
        Transaction(
            reference="CPAY-DATA-NO-COST",
            user_id=transaction.user_id,
            wallet_id=transaction.wallet_id,
            transaction_type="data_purchase",
            category="data",
            amount=Decimal("75.00"),
            currency="NGN",
            charges=Decimal("3.00"),
            total_amount=Decimal("78.00"),
            status="settled",
            provider_name="OtherProvider",
            provider_reference="DATA-REF-001",
        )
    )
    session.add(
        Transaction(
            reference="CPAY-DATA-PENDING-NO-COST",
            user_id=transaction.user_id,
            wallet_id=transaction.wallet_id,
            transaction_type="data_purchase",
            category="data",
            amount=Decimal("25.00"),
            currency="NGN",
            charges=Decimal("1.00"),
            total_amount=Decimal("26.00"),
            status="pending",
            provider_name="PendingProvider",
        )
    )
    await session.flush()

    repository = PlatformIncomeRepository(session=session)
    gaps = await repository.get_cost_evidence_gaps()

    assert gaps == [
        {
            "service_category": "airtime",
            "currency": "NGN",
            "provider_identifier": "VerifiedTestProvider",
            "transaction_count": 1,
            "reason": "No verified transaction-specific provider acquisition-cost evidence.",
        },
        {
            "service_category": "data",
            "currency": "NGN",
            "provider_identifier": "OtherProvider",
            "transaction_count": 1,
            "reason": "No verified transaction-specific provider acquisition-cost evidence.",
        },
    ]

    report = await FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=repository,
    ).get_finance_report(page=1, page_size=20)
    assert [row["service_category"] for row in report["data"]["cost_evidence_unavailable"]] == [
        "airtime",
        "data",
    ]


@pytest.mark.asyncio
async def test_partial_and_full_income_reversals_are_idempotent_and_preserve_wallet(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    repository = PlatformIncomeRepository(session=session)
    _set_verified_airtime_transaction(transaction)
    wallet = await session.get(Wallet, transaction.wallet_id)
    original_amount = transaction.amount
    original_wallet_balance = wallet.available_balance
    income = await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("230.00"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-reversals",
    )

    partial = await repository.record_reversal(
        platform_income_id=income.id,
        amount=Decimal("5.00"),
        currency="NGN",
        event_reference="refund-event-partial",
        source_reference="refund-record-partial",
    )
    duplicate_partial = await repository.record_reversal(
        platform_income_id=income.id,
        amount=Decimal("5.00"),
        currency="NGN",
        event_reference="refund-event-partial",
        source_reference="refund-record-partial",
    )
    full = await repository.record_reversal(
        platform_income_id=income.id,
        amount=Decimal("15.00"),
        currency="NGN",
        event_reference="refund-event-final",
        source_reference="refund-record-final",
    )
    duplicate_full = await repository.record_reversal(
        platform_income_id=income.id,
        amount=Decimal("15.00"),
        currency="NGN",
        event_reference="refund-event-final",
        source_reference="refund-record-final",
    )
    await session.refresh(income)
    events = await session.execute(
        select(PlatformIncomeReversal).where(PlatformIncomeReversal.platform_income_id == income.id)
    )

    assert partial.id == duplicate_partial.id
    assert full.id == duplicate_full.id
    assert len(events.scalars().all()) == 2
    assert income.amount == Decimal("20.00")
    assert income.reversal_amount == Decimal("20.00")
    assert income.reversal_reference == "refund-event-final"
    assert transaction.amount == original_amount
    assert wallet.available_balance == original_wallet_balance
    totals = await repository.get_totals_by_currency()
    assert totals["NGN"] == {
        "gross_amount": Decimal("20.00"),
        "reversal_amount": Decimal("20.00"),
        "net_amount": Decimal("0.00"),
        "loss_amount": Decimal("0.00"),
        "selling_amount": Decimal("250.00"),
        "provider_cost": Decimal("230.00"),
        "gross_service_margin": Decimal("20.00"),
        "recognized_count": 1,
    }
    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=repository,
    )
    report = await finance_service.get_finance_report(page=1, page_size=20)
    assert report["data"]["revenue"]["available"] is True
    assert report["data"]["revenue_by_currency"] == [{
        "currency": "NGN",
        "gross_amount": "20.00",
        "provider_cost": "230.00",
        "selling_amount": "250.00",
        "gross_service_margin": "20.00",
        "loss_amount": "0.00",
        "reversal_amount": "20.00",
        "net_amount": "0.00",
        "recognized_count": 1,
    }]
    details = await finance_service.get_finance_transaction(transaction_id=transaction.id)
    evidence = details["service_margin_evidence"][0]
    assert evidence["cost_source"] == "provider_actual_cost"
    assert evidence["provider_cost"] == "230.00"
    assert evidence["net_amount"] == "0.00"
    assert {item["event_reference"] for item in evidence["reversals"]} == {
        "refund-event-partial",
        "refund-event-final",
    }


@pytest.mark.asyncio
async def test_income_reversals_reject_unattributable_over_refund_and_currency(transaction_services):
    transaction_service, _, transaction = transaction_services
    repository = PlatformIncomeRepository(session=transaction_service.transaction_repository.session)
    _set_verified_airtime_transaction(transaction)
    income = await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("240.00"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-limited",
    )

    with pytest.raises(ValueError, match="cannot exceed"):
        await repository.record_reversal(
            platform_income_id=income.id,
            amount=Decimal("10.01"),
            currency="NGN",
            event_reference="refund-event-too-large",
        )
    with pytest.raises(ValueError, match="currency"):
        await repository.record_reversal(
            platform_income_id=income.id,
            amount=Decimal("1.00"),
            currency="USD",
            event_reference="refund-event-wrong-currency",
        )


@pytest.mark.asyncio
async def test_loss_margin_is_reported_as_cost_evidence_not_zero_revenue(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    repository = PlatformIncomeRepository(session=session)
    _set_verified_airtime_transaction(transaction)
    record = await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("260.00"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="provider-cost-loss",
    )

    assert record.status == "loss"
    assert record.amount == Decimal("0.00")
    assert record.gross_margin == Decimal("-10.00")

    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=repository,
    )
    report = await finance_service.get_finance_report(page=1, page_size=20)
    assert report["data"]["revenue"]["available"] is False
    assert report["data"]["revenue"]["amount"] is None
    assert report["data"]["revenue_by_currency"] == []
    assert report["data"]["service_margin_breakdown"][0]["net_margin"] == "0.00"
    assert report["data"]["service_margin_breakdown"][0]["loss_amount"] == "10.00"


@pytest.mark.asyncio
async def test_platform_income_rejects_failed_transactions_and_currency_mismatch(transaction_services):
    transaction_service, _, transaction = transaction_services
    repository = PlatformIncomeRepository(session=transaction_service.transaction_repository.session)
    failed = await transaction_service.transaction_repository.session.get(
        Transaction,
        UUID("44444444-4444-4444-8444-444444444444"),
    )
    with pytest.raises(ValueError, match="successful transaction"):
        await repository.record_verified_service_margin(
            transaction_id=failed.id,
            provider_cost=Decimal("40.00"),
            provider_cost_currency="NGN",
            service_category="airtime",
            provider_cost_reference="failed-provider-cost",
        )

    _set_verified_airtime_transaction(transaction)
    with pytest.raises(ValueError, match="currency"):
        await repository.record_verified_service_margin(
            transaction_id=transaction.id,
            provider_cost=Decimal("40.00"),
            provider_cost_currency="USD",
            service_category="airtime",
            provider_cost_reference="wrong-currency-cost",
        )


@pytest.mark.asyncio
async def test_revenue_totals_keep_currencies_separate(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    repository = PlatformIncomeRepository(session=session)
    _set_verified_airtime_transaction(transaction)
    usd_transaction = Transaction(
        reference="CPAY-FINANCE-USD",
        user_id=transaction.user_id,
        wallet_id=None,
        transaction_type="airtime_purchase",
        category="airtime",
        amount=Decimal("50.00"),
        currency="USD",
        charges=Decimal("5.00"),
        total_amount=Decimal("55.00"),
        status="completed",
        provider_name="VerifiedTestProvider",
        provider_reference="PROVIDER-TEST-USD",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(usd_transaction)
    await session.flush()
    await repository.record_verified_service_margin(
        transaction_id=transaction.id,
        provider_cost=Decimal("247.50"),
        provider_cost_currency="NGN",
        service_category="airtime",
        provider_cost_reference="test-ngn-income",
    )
    await repository.record_verified_service_margin(
        transaction_id=usd_transaction.id,
        provider_cost=Decimal("48.75"),
        provider_cost_currency="USD",
        service_category="airtime",
        provider_cost_reference="test-usd-income",
    )

    totals = await repository.get_totals_by_currency()

    assert totals["NGN"]["net_amount"] == Decimal("2.50")
    assert totals["USD"]["net_amount"] == Decimal("1.25")
    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=repository,
    )
    report = await finance_service.get_finance_report(page=1, page_size=20)
    assert report["data"]["revenue"]["amount"] is None
    assert report["data"]["revenue"]["currency"] is None
    assert [row["currency"] for row in report["data"]["revenue_by_currency"]] == ["NGN", "USD"]


@pytest.mark.asyncio
async def test_finance_report_activity_filters_and_date_range(transaction_services):
    transaction_service, _, _ = transaction_services
    finance_service = FinanceService(transaction_repository=transaction_service.transaction_repository)

    result = await finance_service.get_finance_report(
        start_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
        end_date=datetime(2026, 2, 2, tzinfo=timezone.utc),
        category="airtime",
        status="failed",
        page=1,
        page_size=20,
    )
    data = result["data"]

    assert data["activity"]["total"] == 1
    assert data["activity"]["items"][0]["reference"] == "CPAY-READ-002"
    assert len(data["currencies"]) == 1
    assert data["currencies"][0]["currency"] == "NGN"
    assert data["currencies"][0]["gross_transaction_volume"] == "0.00"
    assert data["currencies"][0]["failed_transaction_count"] == 1
    assert data["trends"] == []


@pytest.mark.asyncio
async def test_finance_report_separates_configured_estimates_from_verified_revenue(transaction_services):
    transaction_service, _, transaction = transaction_services
    session = transaction_service.transaction_repository.session
    _set_verified_airtime_transaction(transaction)
    transaction.status = "completed"
    snapshot = ProviderCostSnapshot(
        transaction_id=transaction.id,
        provider_identifier="VerifiedTestProvider",
        service_type="airtime",
        provider_reference="PROVIDER-TEST-001",
        selling_amount=Decimal("250.00"),
        currency="NGN",
        cost_amount=Decimal("230.00"),
        gross_margin=Decimal("20.00"),
        source_class="ADMIN_CONFIGURED",
        cost_snapshot_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session.add(snapshot)
    await session.flush()

    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
        platform_income_repository=PlatformIncomeRepository(session=session),
        provider_cost_repository=ProviderAcquisitionCostRepository(session=session),
    )
    report = await finance_service.get_finance_report(
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        category="airtime",
        provider="VerifiedTestProvider",
        currency="NGN",
        page=1,
        page_size=20,
    )
    details = await finance_service.get_finance_transaction(transaction_id=transaction.id)

    assert report["data"]["successful_service_sales_by_currency"] == [
        {"currency": "NGN", "selling_amount": "250.00", "transaction_count": 1}
    ]
    assert report["data"]["estimated_service_margin_by_currency"] == [
        {
            "currency": "NGN",
            "selling_amount": "250.00",
            "provider_cost": "230.00",
            "estimated_margin": "20.00",
            "transaction_count": 1,
        }
    ]
    assert report["data"]["revenue"]["available"] is False
    assert report["data"]["revenue_by_currency"] == []
    assert details["platform_revenue"]["available"] is False
    assert details["provisional_cost_snapshot"]["source_class"] == "ADMIN_CONFIGURED"
    assert details["provisional_cost_snapshot"]["estimated_margin"] == "20.00"


@pytest.mark.asyncio
async def test_wallet_funding_is_not_reported_as_successful_service_sales(transaction_services):
    transaction_service, _, _ = transaction_services
    finance_service = FinanceService(
        transaction_repository=transaction_service.transaction_repository,
    )

    report = await finance_service.get_finance_report(page=1, page_size=20)

    assert report["data"]["successful_service_sales_by_currency"] == []
    assert report["data"]["estimated_service_margin_by_currency"] == []


@pytest.mark.asyncio
async def test_finance_controller_requires_authenticated_admin_context():
    async def get_finance_report(**payload):
        return {"revenue": {"available": False}}

    controller = AdminController(type("Service", (), {"get_finance_report": get_finance_report})())
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/finance",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )

    with pytest.raises(HTTPException) as error:
        await controller.get_finance_report(request=request)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_finance_transaction_detail_omits_user_and_sensitive_metadata(transaction_services):
    transaction_service, _, transaction = transaction_services
    finance_service = FinanceService(transaction_repository=transaction_service.transaction_repository)

    detail = await finance_service.get_finance_transaction(transaction_id=transaction.id)

    assert detail["reference"] == transaction.reference
    assert len(detail["ledger_entries"]) == 1
    assert "user" not in detail
    assert "metadata" not in detail
    assert "provider_transaction_id" not in detail


@pytest.mark.asyncio
async def test_admin_middleware_denies_non_admin_access_to_finance_path():
    middleware = AdminMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/finance",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )
    request.state.user = SimpleNamespace(identity_type="user", user_id=ADMIN_ID, role="admin")
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        return Response("ok", status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 403
    assert called is False


@pytest.mark.asyncio
async def test_admin_middleware_allows_active_admin_finance_access(monkeypatch):
    middleware = AdminMiddleware(lambda scope, receive, send: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/finance",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )
    request.state.user = SimpleNamespace(identity_type="admin", user_id=ADMIN_ID, role="admin")

    async def active_admin(_admin_id):
        return SimpleNamespace(is_active=True, status="active", role="admin")

    monkeypatch.setattr(middleware, "_get_current_admin", active_admin)
    response = await middleware.dispatch(request, lambda _request: _response())

    assert response.status_code == 200


async def _response():
    return Response("ok", status_code=200)
