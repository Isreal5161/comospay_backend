from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bank_account import BankAccount
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.bank_account_repository import BankAccountRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.user.bank_account import BankAccountService
from app.services.security.bank_account_encryption import BankAccountEncryption
from app.services.wallet.withdrawal import WalletWithdrawalService

TEST_PROJECT_REF = "dqptbbwbrrtfkjjhqbug"
WITHDRAWAL_AMOUNT = Decimal("100.00")
INITIAL_AVAILABLE = Decimal("900.00")
INITIAL_LEDGER = Decimal("1000.00")


def get_test_encryption() -> BankAccountEncryption:
    if not os.environ.get("BANK_ACCOUNT_ENCRYPTION_KEY", "").strip():
        pytest.fail("BANK_ACCOUNT_ENCRYPTION_KEY must be configured for PostgreSQL verification.")
    return BankAccountEncryption()


def get_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL is not configured for PostgreSQL verification.")
    parsed = urlsplit(url)
    if parsed.hostname is None or "pooler.supabase.com" not in parsed.hostname:
        pytest.fail("TEST_DATABASE_URL must target the dedicated PostgreSQL test pooler.")
    if TEST_PROJECT_REF not in (parsed.username or ""):
        pytest.fail("TEST_DATABASE_URL does not identify the dedicated test project.")
    if os.environ.get("DATABASE_URL", "").strip():
        pytest.fail("DATABASE_URL must not be present in the PostgreSQL test process.")
    return url


class FakeProviderService:
    def __init__(self, *, result: dict | None = None, error: Exception | None = None, calls: list[dict]):
        self.result = result
        self.error = error
        self.calls = calls

    async def execute_transfer(self, *, operation, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return await operation(SimpleNamespace(code="flutterwave")) if self.result is None else self.result


class FakeProviderAdapter:
    def __init__(self, *, result: dict, calls: list[dict]):
        self.result = result
        self.calls = calls

    async def transfer(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0.05)
        return self.result


async def create_withdrawal_fixture(session: AsyncSession, suffix: str) -> tuple[UUID, UUID, UUID, str]:
    encryption = get_test_encryption()
    user = User(
        email=f"phase4f-{suffix}@example.test",
        username=f"phase4f-{suffix}",
        status="active",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    wallet = Wallet(
        user_id=user.id,
        wallet_reference=f"phase4f-wallet-{suffix}",
        currency="NGN",
        status="active",
        available_balance=INITIAL_AVAILABLE,
        ledger_balance=INITIAL_LEDGER,
        locked_balance=WITHDRAWAL_AMOUNT,
        is_active=True,
    )
    account = BankAccount(
        user_id=user.id,
        account_name="Phase 4F Account",
        account_number_encrypted=encryption.encrypt("0123456789"),
        account_number_fingerprint=encryption.fingerprint("0123456789"),
        account_number_prefix="01",
        account_number_last4="6789",
        bank_name="Test Bank",
        bank_code="044",
        is_active=True,
        status="verified",
        verified_at=datetime.now(timezone.utc),
    )
    session.add_all([wallet, account])
    await session.flush()

    reference = f"phase4f-withdrawal-{suffix}"
    transaction = Transaction(
        reference=reference,
        user_id=user.id,
        wallet_id=wallet.id,
        bank_account_id=account.id,
        transaction_type="wallet_withdrawal_bank",
        category="withdrawal",
        amount=WITHDRAWAL_AMOUNT,
        currency="NGN",
        charges=Decimal("0.00"),
        total_amount=WITHDRAWAL_AMOUNT,
        status="funds_reserved",
        metadata_payload='{"account_number_last4":"6789"}',
    )
    session.add(transaction)
    await session.commit()
    return user.id, wallet.id, transaction.id, reference


async def create_cross_user_fixture(session: AsyncSession, suffix: str) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    encryption = get_test_encryption()
    owner = User(
        email=f"phase4g-owner-{suffix}@example.test",
        username=f"phase4g-owner-{suffix}",
        status="active",
        is_active=True,
    )
    requester = User(
        email=f"phase4g-requester-{suffix}@example.test",
        username=f"phase4g-requester-{suffix}",
        status="active",
        is_active=True,
    )
    session.add_all([owner, requester])
    await session.flush()
    wallet = Wallet(
        user_id=requester.id,
        wallet_reference=f"phase4g-wallet-{suffix}",
        currency="NGN",
        status="active",
        available_balance=INITIAL_AVAILABLE,
        ledger_balance=INITIAL_LEDGER,
        locked_balance=WITHDRAWAL_AMOUNT,
        is_active=True,
    )
    account = BankAccount(
        user_id=owner.id,
        account_name="Phase 4G Account",
        account_number_encrypted=encryption.encrypt("0123456789"),
        account_number_fingerprint=encryption.fingerprint("0123456789"),
        account_number_prefix="01",
        account_number_last4="6789",
        bank_name="Test Bank",
        bank_code="044",
        is_active=True,
        status="verified",
        verified_at=datetime.now(timezone.utc),
    )
    session.add_all([wallet, account])
    await session.flush()
    transaction = Transaction(
        reference=f"phase4g-cross-user-{suffix}",
        user_id=requester.id,
        wallet_id=wallet.id,
        bank_account_id=account.id,
        transaction_type="wallet_withdrawal_bank",
        category="withdrawal",
        amount=WITHDRAWAL_AMOUNT,
        currency="NGN",
        charges=Decimal("0.00"),
        total_amount=WITHDRAWAL_AMOUNT,
        status="funds_reserved",
        metadata_payload='{"account_number_last4":"6789"}',
    )
    session.add(transaction)
    await session.commit()
    return owner.id, requester.id, wallet.id, transaction.id, account.id


async def build_service(
    session: AsyncSession,
    *,
    calls: list[dict],
    provider_calls: list[dict],
    result: dict | None = None,
    error: Exception | None = None,
) -> WalletWithdrawalService:
    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)
    transaction_repository = TransactionRepository(session=session)
    bank_account_service = BankAccountService(
        user_repository=user_repository,
        bank_account_repository=BankAccountRepository(session=session),
        session=session,
            encryption=get_test_encryption(),
    )
    provider_adapter = FakeProviderAdapter(
        result=result or {"provider": "flutterwave", "status": "success", "provider_reference": "phase4f-ref"},
        calls=calls,
    )
    provider_service = FakeProviderService(result=None, error=error, calls=provider_calls)
    return WalletWithdrawalService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        bank_account_service=bank_account_service,
        provider_service=provider_service,
        provider_adapter=provider_adapter,
        session=session,
    )


async def read_state(session: AsyncSession, wallet_id: UUID, transaction_id: UUID) -> tuple[Wallet, Transaction]:
    wallet = (await session.execute(select(Wallet).where(Wallet.id == wallet_id))).scalar_one()
    transaction = (await session.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()
    return wallet, transaction


async def run_concurrent(
    session_factory: async_sessionmaker[AsyncSession],
    transaction_id: UUID,
    *,
    result: dict | None = None,
    error: Exception | None = None,
) -> tuple[list[dict], list[dict]]:
    provider_calls: list[dict] = []
    adapter_calls: list[dict] = []
    async with session_factory() as session_a, session_factory() as session_b:
        service_a = await build_service(session_a, calls=adapter_calls, provider_calls=provider_calls, result=result, error=error)
        service_b = await build_service(session_b, calls=adapter_calls, provider_calls=provider_calls, result=result, error=error)
        first, second = await asyncio.gather(
            service_a.execute_withdrawal(transaction_id=transaction_id),
            service_b.execute_withdrawal(transaction_id=transaction_id),
        )
        assert {first["status"], second["status"]} <= {"completed", "failed", "pending"}
    return provider_calls, adapter_calls


@pytest.mark.asyncio
async def test_concurrent_execution_has_one_success_settlement():
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=3)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, reference = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
        provider_calls, adapter_calls = await run_concurrent(session_factory, transaction_id)
        assert len(provider_calls) == 1
        assert len(adapter_calls) == 1
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, wallet_id, transaction_id)
            assert transaction.status == "completed"
            assert wallet.available_balance == INITIAL_AVAILABLE
            assert wallet.locked_balance == Decimal("0.00")
            assert wallet.ledger_balance == Decimal("900.00")
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.id.in_(wallet_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_confirmed_failure_releases_once():
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=3)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, _ = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
        provider_calls, adapter_calls = await run_concurrent(
            session_factory,
            transaction_id,
            result={"provider": "flutterwave", "status": "failed"},
        )
        assert len(provider_calls) == 1
        assert len(adapter_calls) == 1
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, next(iter(wallet_ids)), transaction_id)
            assert transaction.status == "failed"
            assert wallet.available_balance == Decimal("1000.00")
            assert wallet.locked_balance == Decimal("0.00")
            assert wallet.ledger_balance == INITIAL_LEDGER
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.user_id.in_(user_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
async def test_timeout_is_pending_and_does_not_call_adapter_or_failover():
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, _ = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
        provider_calls: list[dict] = []
        adapter_calls: list[dict] = []
        async with session_factory() as session:
            service = await build_service(
                session,
                calls=adapter_calls,
                provider_calls=provider_calls,
                error=TimeoutError("provider outcome is unknown"),
            )
            result = await service.execute_withdrawal(transaction_id=transaction_id)
        assert result["status"] == "pending"
        assert len(provider_calls) == 1
        assert provider_calls[0]["retryable_errors"] == ()
        assert adapter_calls == []
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, next(iter(wallet_ids)), transaction_id)
            assert transaction.status == "pending"
            assert wallet.available_balance == INITIAL_AVAILABLE
            assert wallet.locked_balance == WITHDRAWAL_AMOUNT
            assert wallet.ledger_balance == INITIAL_LEDGER
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.user_id.in_(user_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_reexecution_preserves_reference_after_timeout():
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, reference = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
        first_provider_calls: list[dict] = []
        first_adapter_calls: list[dict] = []
        async with session_factory() as first_session:
            first_service = await build_service(
                first_session,
                calls=first_adapter_calls,
                provider_calls=first_provider_calls,
                error=TimeoutError("provider outcome is unknown"),
            )
            first = await first_service.execute_withdrawal(transaction_id=transaction_id)
        second_provider_calls: list[dict] = []
        second_adapter_calls: list[dict] = []
        async with session_factory() as second_session:
            second_service = await build_service(
                second_session,
                calls=second_adapter_calls,
                provider_calls=second_provider_calls,
            )
            second = await second_service.execute_withdrawal(transaction_id=transaction_id)
        assert first["status"] == "pending"
        assert second["status"] == "completed"
        assert second["reference"] == reference
        assert second_provider_calls[0]["payload"]["reference"] == reference
        assert second_adapter_calls[0]["reference"] == reference
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.user_id.in_(user_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_result, expected_status", [
    ({"provider": "flutterwave", "status": "success"}, "completed"),
    ({"provider": "flutterwave", "status": "failed"}, "failed"),
])
async def test_terminal_duplicate_execution_does_not_call_provider(terminal_result, expected_status):
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, _ = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
        calls: list[dict] = []
        provider_calls: list[dict] = []
        async with session_factory() as first_session:
            service = await build_service(
                first_session,
                calls=calls,
                provider_calls=provider_calls,
                result=terminal_result,
            )
            first = await service.execute_withdrawal(transaction_id=transaction_id)
        async with session_factory() as second_session:
            duplicate_service = await build_service(
                second_session,
                calls=calls,
                provider_calls=provider_calls,
                result=terminal_result,
            )
            duplicate = await duplicate_service.execute_withdrawal(transaction_id=transaction_id)
        assert first["status"] == expected_status
        assert duplicate["status"] == expected_status
        assert len(provider_calls) == 1
        assert len(calls) == 1
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.user_id.in_(user_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_user_beneficiary_is_rejected_before_provider_execution():
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    account_id: UUID | None = None
    try:
        async with session_factory() as setup:
            owner_id, requester_id, wallet_id, transaction_id, account_id = await create_cross_user_fixture(
                setup,
                uuid4().hex[:12],
            )
            user_ids.add(owner_id)
            user_ids.add(requester_id)
            wallet_ids.add(wallet_id)
        provider_calls: list[dict] = []
        adapter_calls: list[dict] = []
        async with session_factory() as session:
            service = await build_service(session, calls=adapter_calls, provider_calls=provider_calls)
            with pytest.raises(Exception, match="Bank account not found"):
                await service.execute_withdrawal(
                    transaction_id=transaction_id,
                    authenticated_user_id=requester_id,
                )
        assert provider_calls == []
        assert adapter_calls == []
        async with session_factory() as verify:
            wallet_id = next(iter(wallet_ids))
            wallet, transaction = await read_state(verify, wallet_id, transaction_id)
            assert transaction.status == "funds_reserved"
            assert wallet.available_balance == INITIAL_AVAILABLE
            assert wallet.locked_balance == WITHDRAWAL_AMOUNT
            assert wallet.ledger_balance == INITIAL_LEDGER
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                if account_id is not None:
                    await cleanup.execute(delete(BankAccount).where(BankAccount.id == account_id))
                await cleanup.execute(delete(Wallet).where(Wallet.id.in_(wallet_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_status, is_active", [("removed", False), ("pending", True)])
async def test_inactive_or_unverified_beneficiary_is_rejected(account_status, is_active):
    url = get_test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    transaction_id: UUID | None = None
    try:
        async with session_factory() as setup:
            user_id, wallet_id, transaction_id, _ = await create_withdrawal_fixture(setup, uuid4().hex[:12])
            user_ids.add(user_id)
            wallet_ids.add(wallet_id)
            account_id = (await setup.execute(
                select(Transaction.bank_account_id).where(Transaction.id == transaction_id)
            )).scalar_one()
            await setup.execute(
                BankAccount.__table__.update()
                .where(BankAccount.id == account_id)
                .values(status=account_status, is_active=is_active, verified_at=None)
            )
            await setup.commit()
        provider_calls: list[dict] = []
        adapter_calls: list[dict] = []
        async with session_factory() as session:
            service = await build_service(session, calls=adapter_calls, provider_calls=provider_calls)
            with pytest.raises(Exception, match="not verified"):
                await service.execute_withdrawal(
                    transaction_id=transaction_id,
                    authenticated_user_id=user_id,
                )
        assert provider_calls == []
        assert adapter_calls == []
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, next(iter(wallet_ids)), transaction_id)
            assert transaction.status == "funds_reserved"
            assert wallet.available_balance == INITIAL_AVAILABLE
            assert wallet.locked_balance == WITHDRAWAL_AMOUNT
            assert wallet.ledger_balance == INITIAL_LEDGER
    finally:
        async with session_factory() as cleanup:
            async with cleanup.begin():
                if transaction_id is not None:
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                await cleanup.execute(delete(BankAccount).where(BankAccount.user_id.in_(user_ids)))
                await cleanup.execute(delete(Wallet).where(Wallet.user_id.in_(user_ids)))
                await cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()
