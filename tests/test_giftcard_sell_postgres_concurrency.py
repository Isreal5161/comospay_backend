from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.giftcard.trading import GiftCardTradingService
from app.utils.exceptions import WalletException

TEST_PROJECT_REF = "dqptbbwbrrtfkjjhqbug"
PAYOUT = Decimal("125000.00")
INITIAL_BALANCE = Decimal("1000000.00")


def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("TEST_DATABASE_URL must be an explicit PostgreSQL asyncpg URL.")
    if TEST_PROJECT_REF not in (url.split("@", 1)[0]):
        raise RuntimeError("TEST_DATABASE_URL does not identify the dedicated test project.")
    if os.environ.get("DATABASE_URL", "").strip():
        raise RuntimeError("DATABASE_URL must not be present in the test process.")
    return url


class LocalProviderService:
    """Return a synthetic normalized result without contacting a provider."""

    async def execute_giftcard(self, *, operation, validate, normalize, payload):
        validate(payload or {})
        provider = SimpleNamespace(id=uuid4(), name="synthetic-test-provider")
        result = await operation(provider)
        return normalize(result, provider)


class ObservingTransactionRepository(TransactionRepository):
    def __init__(self, session: AsyncSession, *, acquired: asyncio.Event | None = None, hold_seconds: float = 0):
        super().__init__(session)
        self.acquired = acquired
        self.hold_seconds = hold_seconds
        self.backend_pid: int | None = None
        self.lock_acquired_at: float | None = None

    async def get_by_reference_for_update(self, reference: str):
        started = time.perf_counter()
        result = await super().get_by_reference_for_update(reference)
        self.lock_acquired_at = time.perf_counter()
        self.backend_pid = int((await self.session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        if self.acquired is not None:
            self.acquired.set()
        if self.hold_seconds:
            await asyncio.sleep(self.hold_seconds)
        self.wait_seconds = self.lock_acquired_at - started
        return result


def make_service(session: AsyncSession, transaction_repository: TransactionRepository) -> GiftCardTradingService:
    return GiftCardTradingService(
        wallet_service=SimpleNamespace(),
        provider_service=LocalProviderService(),
        transaction_repository=transaction_repository,
        user_repository=SimpleNamespace(),
        wallet_repository=WalletRepository(session),
    )


@pytest.mark.asyncio
async def test_service_transaction_scope_is_safe_when_session_already_has_active_transaction() -> None:
    url = test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with session_factory() as session:
            repository = TransactionRepository(session)
            service = make_service(session, repository)
            async with session.begin():
                assert session.in_transaction() is True
                async with service._transaction_scope():
                    assert session.in_transaction() is True
    finally:
        await engine.dispose()


async def create_sell(session: AsyncSession, reference: str, *, wallet_currency: str = "NGN", payout_currency: str = "NGN") -> tuple[UUID, UUID, UUID]:
    user = User(email=f"{reference.lower()}@example.test", username=reference.lower(), status="active", is_active=True)
    session.add(user)
    await session.flush()
    wallet = Wallet(
        user_id=user.id,
        wallet_reference=f"wallet-{reference.lower()}",
        currency=wallet_currency,
        status="active",
        available_balance=INITIAL_BALANCE,
        ledger_balance=INITIAL_BALANCE,
        locked_balance=Decimal("0.00"),
        is_active=True,
    )
    session.add(wallet)
    await session.flush()
    transaction = Transaction(
        reference=reference,
        user_id=user.id,
        wallet_id=wallet.id,
        transaction_type="giftcard_sell",
        category="giftcard",
        amount=Decimal("100.00"),
        currency="USD",
        charges=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        card_amount=Decimal("100.00"),
        card_currency="USD",
        payout_amount=PAYOUT,
        payout_currency=payout_currency,
        status="pending",
        credit_applied=False,
        metadata_payload='{"brand":"amazon","card_type":"ecode"}',
    )
    session.add(transaction)
    await session.commit()
    return user.id, wallet.id, transaction.id


async def read_state(session: AsyncSession, wallet_id: UUID, transaction_id: UUID) -> tuple[Wallet, Transaction]:
    wallet = (await session.execute(select(Wallet).where(Wallet.id == wallet_id))).scalar_one()
    transaction = (await session.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()
    return wallet, transaction


async def cleanup(session_factory, user_ids: set[UUID], wallet_ids: set[UUID], references: set[str]) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(Transaction).where(Transaction.reference.in_(references)))
            await session.execute(delete(Wallet).where(Wallet.id.in_(wallet_ids)))
            await session.execute(delete(User).where(User.id.in_(user_ids)))


async def main() -> None:
    url = test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_ids: set[UUID] = set()
    wallet_ids: set[UUID] = set()
    references: set[str] = set()
    try:
        async with session_factory() as setup:
            database = (await setup.execute(text("SELECT current_database()"))).scalar_one()
            if database != "postgres":
                raise AssertionError("Unexpected test database name")
            prefix = f"PHASE2E-{uuid4().hex[:12].upper()}"
            reference = f"{prefix}-CONCURRENCY"
            references.add(reference)
            user_id, wallet_id, transaction_id = await create_sell(setup, reference)
            user_ids.add(user_id); wallet_ids.add(wallet_id)
        async with session_factory() as snapshot_session_a, session_factory() as snapshot_session_b:
            transaction_snapshot_a = (await snapshot_session_a.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()
            transaction_snapshot_b = (await snapshot_session_b.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()

        acquired = asyncio.Event()
        async with session_factory() as session_a, session_factory() as session_b:
            repository_a = ObservingTransactionRepository(session_a, acquired=acquired, hold_seconds=0.5)
            repository_b = ObservingTransactionRepository(session_b)
            service_a = make_service(session_a, repository_a)
            service_b = make_service(session_b, repository_b)

            async def attempt(service, transaction_snapshot):
                return await service.process_giftcard_trade(
                    transaction=transaction_snapshot,
                    provider_name="synthetic-test-provider",
                    provider_operation=lambda provider: asyncio.sleep(
                        0, result={"status": "success", "payout_amount": {"raw": 125000, "currency": "NGN"}, "provider_reference": "phase2e-provider"}
                    ),
                )

            task_a = asyncio.create_task(attempt(service_a, transaction_snapshot_a))
            await asyncio.wait_for(acquired.wait(), timeout=10)
            task_b = asyncio.create_task(attempt(service_b, transaction_snapshot_b))
            result_a, result_b = await asyncio.gather(task_a, task_b)
            if repository_a.backend_pid == repository_b.backend_pid:
                raise AssertionError("Workers did not use independent PostgreSQL sessions")
            if repository_b.lock_acquired_at is None or repository_a.lock_acquired_at is None:
                raise AssertionError("Could not observe transaction row lock acquisition")
            if repository_b.lock_acquired_at - repository_a.lock_acquired_at < 0.20:
                raise AssertionError("PostgreSQL lock contention was not observed")
            print(f"concurrency_session_pids_distinct={repository_a.backend_pid != repository_b.backend_pid}")
            print(f"concurrent_results={result_a['status']},{result_b['status']}")
            print(f"lock_wait_observed={repository_b.lock_acquired_at - repository_a.lock_acquired_at >= 0.20}")

        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, wallet_id, transaction_id)
            assert wallet.available_balance == Decimal("1125000.00")
            assert wallet.ledger_balance == Decimal("1125000.00")
            assert transaction.credit_applied is True
            assert transaction.credited_amount == PAYOUT
            assert transaction.credited_currency == "NGN"
            assert transaction.payout_amount == PAYOUT
            assert transaction.payout_currency == "NGN"
            ledger_count = (await verify.execute(text("SELECT COUNT(*) FROM ledgers WHERE transaction_reference = :reference"), {"reference": reference})).scalar_one()
            print(f"final_available_balance={wallet.available_balance}")
            print(f"final_ledger_balance={wallet.ledger_balance}")
            print(f"credit_record_count={ledger_count}")
            print("exactly_once_credit=True")

        async with session_factory() as duplicate_session:
            duplicate_service = make_service(duplicate_session, TransactionRepository(duplicate_session))
            duplicate_snapshot = (await duplicate_session.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()
            await duplicate_session.rollback()
            await duplicate_service.process_giftcard_trade(
                transaction=duplicate_snapshot,
                provider_name="synthetic-test-provider",
                provider_operation=lambda provider: asyncio.sleep(0, result={"status": "success", "payout_amount": {"raw": 125000, "currency": "NGN"}, "provider_reference": "phase2e-provider"}),
            )
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, wallet_id, transaction_id)
            assert wallet.available_balance == Decimal("1125000.00")
            assert transaction.credit_applied is True
            print("repeated_completion_additional_credit=0")

        rollback_reference = f"{prefix}-ROLLBACK"; references.add(rollback_reference)
        async with session_factory() as setup:
            rollback_user, rollback_wallet, rollback_transaction = await create_sell(setup, rollback_reference)
            user_ids.add(rollback_user); wallet_ids.add(rollback_wallet)
        try:
            async with session_factory() as rollback_session:
                repository = TransactionRepository(rollback_session)
                async with rollback_session.begin():
                    locked = await repository.get_by_reference_for_update(rollback_reference)
                    service = make_service(rollback_session, repository)
                    await service._handle_successful_trade(locked)
                    raise RuntimeError("controlled test rollback")
        except RuntimeError as exc:
            if str(exc) != "controlled test rollback":
                raise
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, rollback_wallet, rollback_transaction)
            assert wallet.available_balance == INITIAL_BALANCE
            assert wallet.ledger_balance == INITIAL_BALANCE
            assert transaction.credit_applied is False
            assert transaction.credited_amount is None
            print("rollback_passed=True")

        currency_reference = f"{prefix}-CURRENCY"; references.add(currency_reference)
        async with session_factory() as setup:
            currency_user, currency_wallet, currency_transaction = await create_sell(setup, currency_reference, payout_currency="USD")
            user_ids.add(currency_user); wallet_ids.add(currency_wallet)
        async with session_factory() as currency_snapshot_session:
            currency_snapshot = (await currency_snapshot_session.execute(select(Transaction).where(Transaction.id == currency_transaction))).scalar_one()
        try:
            async with session_factory() as currency_session:
                service = make_service(currency_session, TransactionRepository(currency_session))
                await service.process_giftcard_trade(
                    transaction=currency_snapshot,
                    provider_name="synthetic-test-provider",
                    provider_operation=lambda provider: asyncio.sleep(0, result={"status": "success", "payout_amount": {"raw": 125000, "currency": "USD"}, "provider_reference": "phase2e-currency"}),
                )
        except WalletException:
            pass
        else:
            raise AssertionError("Currency mismatch was not rejected")
        async with session_factory() as verify:
            wallet, transaction = await read_state(verify, currency_wallet, currency_transaction)
            assert wallet.available_balance == INITIAL_BALANCE
            assert transaction.credit_applied is False
            print("currency_mismatch_rejected=True")
    finally:
        await cleanup(session_factory, user_ids, wallet_ids, references)
        async with session_factory() as verify:
            remaining = (await verify.execute(select(Transaction.reference).where(Transaction.reference.in_(references)))).scalars().all()
            if remaining:
                raise AssertionError(f"Synthetic records remain: {remaining}")
        await engine.dispose()
        print("cleanup_verified=True")


if __name__ == "__main__":
    asyncio.run(main())