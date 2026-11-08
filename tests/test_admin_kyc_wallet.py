from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.audit_log import AuditLog
from app.models.kyc import KYC
from app.models.ledger import Ledger
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.kyc_repository import KYCRepository
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.routes import admin_routes
from app.services.admin.kyc import KYCService as AdminKYCService
from app.services.admin.wallet import WalletAdministrationService
from app.services.ledger_service import LedgerService
from app.services.user.kyc import KYCService as UserKYCService
from app.services.wallet.wallet_balance import WalletBalanceService
from app.utils.exceptions import DatabaseException, ValidationException, WalletException


ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


class ActiveAdminRepository:
    async def get_by_id(self, admin_id: UUID):
        if admin_id != ADMIN_ID:
            return None
        return SimpleNamespace(is_active=True, status="active", role="admin")


@pytest_asyncio.fixture
async def database_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def make_user(email: str) -> User:
    return User(email=email, password_hash="test-hash", status="active")


def make_kyc(user_id: UUID, *, status: str = "pending", created_at=None) -> KYC:
    return KYC(
        user_id=user_id,
        verification_status=status,
        verification_level="basic",
        submitted_at=created_at,
        created_at=created_at,
    )


def make_kyc_service(session) -> AdminKYCService:
    user_repository = UserRepository(session)
    kyc_repository = KYCRepository(session)
    domain_service = UserKYCService(
        user_repository=user_repository,
        kyc_repository=kyc_repository,
        session=session,
    )
    return AdminKYCService(
        kyc_domain_service=domain_service,
        kyc_repository=kyc_repository,
        admin_repository=ActiveAdminRepository(),
        audit_repository=AuditLogRepository(session),
        session=session,
    )


async def add_user(session, email: str) -> User:
    user = make_user(email)
    session.add(user)
    await session.flush()
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_admin_kyc_review_updates_the_selected_submission_only(database_session):
    session = database_session
    user = await add_user(session, "kyc-user@example.test")
    submission_a = make_kyc(user.id)
    submission_b = make_kyc(user.id)
    session.add_all([submission_a, submission_b])
    await session.flush()
    selected_id = submission_a.id

    service = make_kyc_service(session)
    response = await service.review_kyc(
        user_id=user.id,
        kyc_id=selected_id,
        action="approved",
        admin_id=ADMIN_ID,
    )

    saved_a = await session.get(KYC, submission_a.id)
    saved_b = await session.get(KYC, submission_b.id)
    assert response["data"]["id"] == str(selected_id)
    assert response["data"]["verification_status"] == "approved"
    assert saved_a.verification_status == "approved"
    assert saved_a.reviewed_by == str(ADMIN_ID)
    assert saved_a.reviewed_at is not None
    assert saved_b.verification_status == "pending"
    audit = await session.scalar(select(AuditLog).where(AuditLog.resource_id == str(selected_id)))
    assert audit is not None
    assert audit.actor_id == str(ADMIN_ID)


@pytest.mark.asyncio
async def test_admin_kyc_review_rejects_submission_owned_by_another_user(database_session):
    session = database_session
    supplied_user = await add_user(session, "supplied@example.test")
    owner = await add_user(session, "owner@example.test")
    submission = make_kyc(owner.id)
    session.add(submission)
    await session.flush()
    await session.commit()
    submission_id = submission.id

    with pytest.raises(ValidationException) as exc_info:
        await make_kyc_service(session).review_kyc(
            user_id=supplied_user.id,
            kyc_id=submission_id,
            action="approved",
            admin_id=ADMIN_ID,
        )
    assert exc_info.value.error_code == "KYC_NOT_FOUND"
    await session.rollback()
    assert (await session.get(KYC, submission_id)).verification_status == "pending"


@pytest.mark.asyncio
async def test_admin_kyc_review_rejects_nonexistent_submission(database_session):
    session = database_session
    user = await add_user(session, "missing-kyc@example.test")

    with pytest.raises(ValidationException) as exc_info:
        await make_kyc_service(session).review_kyc(
            user_id=user.id,
            kyc_id=uuid4(),
            action="approved",
            admin_id=ADMIN_ID,
        )
    assert exc_info.value.error_code == "KYC_NOT_FOUND"


@pytest.mark.asyncio
async def test_admin_kyc_review_rejects_repeated_terminal_decision(database_session):
    session = database_session
    user = await add_user(session, "terminal-kyc@example.test")
    submission = make_kyc(user.id, status="approved")
    session.add(submission)
    await session.flush()
    await session.commit()
    submission_id = submission.id

    with pytest.raises(ValidationException, match="already been finalized"):
        await make_kyc_service(session).review_kyc(
            user_id=user.id,
            kyc_id=submission_id,
            action="rejected",
            admin_id=ADMIN_ID,
            review_notes="Conflicting decision",
        )
    await session.rollback()
    assert (await session.get(KYC, submission_id)).verification_status == "approved"


@pytest.mark.asyncio
async def test_admin_kyc_rejection_records_reviewer_time_and_reason(database_session):
    session = database_session
    user = await add_user(session, "rejected-kyc@example.test")
    submission = make_kyc(user.id)
    session.add(submission)
    await session.flush()

    response = await make_kyc_service(session).review_kyc(
        user_id=user.id,
        kyc_id=submission.id,
        action="rejected",
        admin_id=ADMIN_ID,
        review_notes="Document image is not legible",
    )

    assert response["data"]["verification_status"] == "rejected"
    assert response["data"]["reviewed_by"] == str(ADMIN_ID)
    assert response["data"]["reviewed_at"] is not None
    assert response["data"]["rejection_reason"] == "Document image is not legible"


@pytest.mark.asyncio
async def test_admin_kyc_rejection_requires_a_reason(database_session):
    session = database_session
    user = await add_user(session, "missing-reason@example.test")
    submission = make_kyc(user.id)
    session.add(submission)
    await session.flush()
    await session.commit()
    submission_id = submission.id

    with pytest.raises(ValidationException, match="Rejection reason is required"):
        await make_kyc_service(session).review_kyc(
            user_id=user.id,
            kyc_id=submission_id,
            action="rejected",
            admin_id=ADMIN_ID,
            review_notes="  ",
        )
    await session.rollback()
    assert (await session.get(KYC, submission_id)).verification_status == "pending"


@pytest.mark.asyncio
async def test_admin_kyc_and_user_scoped_transaction_routes_are_registered():
    paths = {(route.path, method) for route in admin_routes.router.routes for method in route.methods or []}
    assert ("/admin/users/{user_id}/kyc/{kyc_id}/review", "POST") in paths
    assert ("/admin/users/{user_id}/transactions", "GET") in paths
    assert ("/admin/transactions", "GET") in paths


def make_wallet_service(session, *, wallet_repository: WalletRepository | None = None, ledger_service=None):
    wallets = wallet_repository or WalletRepository(session)
    ledgers = LedgerRepository(session)
    ledger_domain = ledger_service or LedgerService(
        ledger_repository=ledgers,
        wallet_repository=wallets,
        user_repository=UserRepository(session),
    )
    balance_service = WalletBalanceService(
        wallet_repository=wallets,
        ledger_service=ledger_domain,
        session=session,
    )
    return WalletAdministrationService(
        wallet_repository=wallets,
        ledger_repository=ledgers,
        admin_repository=ActiveAdminRepository(),
        audit_repository=AuditLogRepository(session),
        wallet_balance_service=balance_service,
        session=session,
    ), ledger_domain


async def add_wallet(session, user: User, *, currency: str = "NGN", balance: str = "100.00") -> Wallet:
    amount = Decimal(balance)
    wallet = Wallet(
        user_id=user.id,
        currency=currency,
        available_balance=amount,
        ledger_balance=amount,
        locked_balance=Decimal("0.00"),
    )
    session.add(wallet)
    await session.flush()
    await session.commit()
    return wallet


async def adjust(service, user: User, wallet: Wallet, *, amount=Decimal("10.00"), currency="NGN", reference="adjustment-1"):
    return await service.adjust_wallet(
        user_id=user.id,
        wallet_id=wallet.id,
        amount=amount,
        currency=currency,
        reason="approved support adjustment",
        reference=reference,
        admin_id=ADMIN_ID,
    )


@pytest.mark.asyncio
async def test_admin_wallet_adjustment_rejects_wallet_owned_by_another_user(database_session):
    session = database_session
    first_user = await add_user(session, "wallet-first@example.test")
    second_user = await add_user(session, "wallet-second@example.test")
    first_wallet = await add_wallet(session, first_user)
    second_wallet = await add_wallet(session, second_user)
    first_user_id = first_user.id
    second_user_id = second_user.id
    first_wallet_id = first_wallet.id
    second_wallet_id = second_wallet.id
    service, _ = make_wallet_service(session)

    with pytest.raises(ValidationException) as exc_info:
        await adjust(service, first_user, second_wallet)
    assert exc_info.value.error_code == "WALLET_NOT_FOUND"
    await session.rollback()
    assert (await session.get(Wallet, second_wallet_id)).available_balance == Decimal("100.00")
    assert list((await session.scalars(select(Ledger))).all()) == []
    assert list((await session.scalars(select(AuditLog))).all()) == []
    assert first_user_id != second_user_id
    assert first_wallet_id != second_wallet_id


@pytest.mark.asyncio
async def test_admin_wallet_adjustment_rejects_currency_mismatch(database_session):
    session = database_session
    user = await add_user(session, "wallet-currency@example.test")
    wallet = await add_wallet(session, user, currency="USD")
    wallet_id = wallet.id
    service, _ = make_wallet_service(session)

    with pytest.raises(ValidationException) as exc_info:
        await adjust(service, user, wallet, currency="NGN")
    assert exc_info.value.error_code == "WALLET_CURRENCY_MISMATCH"
    await session.rollback()
    assert (await session.get(Wallet, wallet_id)).available_balance == Decimal("100.00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amount",
    [Decimal("0"), Decimal("NaN"), Decimal("1.001"), Decimal("1000000000.00")],
)
async def test_admin_wallet_adjustment_rejects_invalid_amounts(database_session, amount):
    session = database_session
    user = await add_user(session, f"wallet-invalid-{str(amount).replace('.', '-')}@example.test")
    wallet = await add_wallet(session, user)
    wallet_id = wallet.id
    service, _ = make_wallet_service(session)

    with pytest.raises(ValidationException):
        await adjust(service, user, wallet, amount=amount)
    await session.rollback()
    assert (await session.get(Wallet, wallet_id)).available_balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_admin_wallet_adjustment_is_idempotent_and_audited(database_session):
    session = database_session
    user = await add_user(session, "wallet-idempotent@example.test")
    wallet = await add_wallet(session, user)
    wallet_id = wallet.id
    service, _ = make_wallet_service(session)

    first = await adjust(service, user, wallet, reference="support-adjustment-42")
    second = await adjust(service, user, wallet, reference="support-adjustment-42")

    saved_wallet = await session.get(Wallet, wallet.id)
    ledger_entries = list((await session.scalars(select(Ledger))).all())
    audit_entries = list((await session.scalars(select(AuditLog))).all())
    assert first["data"]["idempotent"] is False
    assert second["data"]["idempotent"] is True
    assert saved_wallet.available_balance == Decimal("110.00")
    assert saved_wallet.ledger_balance == Decimal("110.00")
    assert len(ledger_entries) == 1
    assert ledger_entries[0].created_by == f"admin:{ADMIN_ID}"
    assert ledger_entries[0].currency == "NGN"
    assert ledger_entries[0].metadata_payload["reason"] == "approved support adjustment"
    assert len(audit_entries) == 1
    assert audit_entries[0].actor_id == str(ADMIN_ID)
    audit_data = json.loads(audit_entries[0].new_value)
    assert audit_data["amount"] == "10.00"
    assert audit_data["currency"] == "NGN"
    assert audit_data["reason"] == "approved support adjustment"
    assert audit_data["reference"] == "support-adjustment-42"


@pytest.mark.asyncio
async def test_admin_wallet_adjustment_rejects_reused_reference_with_different_request(database_session):
    session = database_session
    user = await add_user(session, "wallet-reference-conflict@example.test")
    wallet = await add_wallet(session, user)
    wallet_id = wallet.id
    service, _ = make_wallet_service(session)
    await adjust(service, user, wallet, reference="same-reference")

    with pytest.raises(ValidationException) as exc_info:
        await adjust(service, user, wallet, amount=Decimal("20.00"), reference="same-reference")
    assert exc_info.value.error_code == "IDEMPOTENCY_REFERENCE_CONFLICT"
    await session.rollback()
    assert (await session.get(Wallet, wallet_id)).available_balance == Decimal("110.00")


@pytest.mark.asyncio
async def test_admin_wallet_debit_cannot_overdraw_available_balance(database_session):
    session = database_session
    user = await add_user(session, "wallet-overdraft@example.test")
    wallet = await add_wallet(session, user)
    wallet_id = wallet.id
    service, _ = make_wallet_service(session)

    with pytest.raises(WalletException):
        await adjust(service, user, wallet, amount=Decimal("-101.00"), reference="overdraft")
    saved_wallet = await session.get(Wallet, wallet_id)
    assert saved_wallet.available_balance == Decimal("100.00")
    assert list((await session.scalars(select(Ledger))).all()) == []
    assert list((await session.scalars(select(AuditLog))).all()) == []


@pytest.mark.asyncio
async def test_admin_wallet_adjustment_rolls_back_balance_ledger_and_audit_together(database_session):
    session = database_session
    user = await add_user(session, "wallet-atomic@example.test")
    wallet = await add_wallet(session, user)
    wallet_id = wallet.id
    service, ledger_domain = make_wallet_service(session)

    async def fail_ledger_creation(**_kwargs):
        raise RuntimeError("simulated ledger persistence failure")

    ledger_domain.create_entry = fail_ledger_creation
    with pytest.raises(DatabaseException):
        await adjust(service, user, wallet, reference="atomic-failure")

    saved_wallet = await session.get(Wallet, wallet_id)
    assert saved_wallet.available_balance == Decimal("100.00")
    assert list((await session.scalars(select(Ledger))).all()) == []
    assert list((await session.scalars(select(AuditLog))).all()) == []


@pytest.mark.asyncio
async def test_wallet_repository_uses_postgresql_row_lock_for_adjustments():
    class CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    session = CaptureSession()
    await WalletRepository(session).get_by_id_for_update(uuid4())
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
