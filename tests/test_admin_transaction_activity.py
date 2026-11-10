from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.ledger import Ledger
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.admin.transactions import TransactionAdministrationService
from app.services.admin.payments import PaymentAdministrationService
from app.services.admin.wallet import WalletAdministrationService
from app.utils.exceptions import AuthorizationException, ValidationException

ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


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
