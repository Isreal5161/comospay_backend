import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.database.base import Base
from app.jobs import virtual_account_retry_job as retry_job_module
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet
from app.repositories.user_repository import UserRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.virtual_account_service import VirtualAccountService


def arun(coro):
    return asyncio.run(coro)


def arun_gather(*coros):
    async def _inner():
        return await asyncio.gather(*coros)

    return asyncio.run(_inner())


class FakeProviderService:
    def __init__(self, responses: list[Any]):
        self.responses = responses
        self.calls = 0

    async def create_virtual_account(self, customer: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        result = self.responses[self.calls - 1]
        if isinstance(result, Exception):
            raise result
        if inspect.isawaitable(result):
            return await result
        return result


class FakeRedis:
    def __init__(self, allow_first_lock: bool = True):
        self.calls = 0
        self.allow_first_lock = allow_first_lock

    async def set(self, key, value, nx=False, ex=None):
        self.calls += 1
        if self.allow_first_lock:
            return self.calls == 1
        return False

    async def delete(self, key):
        return 1


@pytest.fixture
def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def prepare():
        async with engine.begin() as conn:
            for table in list(Base.metadata.tables.values()):
                seen = set()
                for idx in list(table.indexes):
                    if idx.name in seen:
                        try:
                            table.indexes.remove(idx)
                        except KeyError:
                            pass
                        try:
                            Base.metadata.indexes.remove(idx)
                        except Exception:
                            pass
                    else:
                        seen.add(idx.name)

            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(prepare())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def test_session(test_engine):
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, autoflush=False)
    session = asyncio.run(session_factory().__aenter__())
    yield session
    asyncio.run(session.rollback())
    asyncio.run(session.__aexit__(None, None, None))


async def create_user_and_wallet(session: AsyncSession) -> tuple[User, Wallet]:
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        username=f"user-{uuid4().hex[:8]}",
        status="active",
        is_active=True,
    )
    wallet = Wallet(
        id=uuid4(),
        user_id=user.id,
        currency="NGN",
        status="active",
        available_balance=0,
        ledger_balance=0,
        locked_balance=0,
    )
    session.add_all([user, wallet])
    await session.flush()
    await session.refresh(user)
    await session.refresh(wallet)
    return user, wallet


async def create_pending_virtual_account(
    session: AsyncSession,
    wallet: Wallet,
    user: User,
    *,
    retry_count: int = 0,
    next_retry_at: datetime | None = None,
    status: str = "PENDING",
    metadata: dict[str, Any] | None = None,
) -> VirtualAccount:
    account = VirtualAccount(
        id=uuid4(),
        wallet_id=wallet.id,
        user_id=user.id,
        provider="flutterwave",
        account_number=f"PENDING{uuid4().hex[:10]}",
        account_name="Test Account",
        bank_name="Test Bank",
        currency="NGN",
        status=status,
        retry_count=retry_count,
        last_retry_at=None,
        next_retry_at=next_retry_at,
        last_error=None,
        provisioned_at=None,
        metadata_payload=metadata or {},
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


def build_retry_job(
    session: AsyncSession,
    provider_service: Any,
    redis_client: Any | None = None,
    max_per_batch: int = 100,
    max_retries: int | None = None,
    logger: logging.Logger | None = None,
) -> VirtualAccountRetryJob:
    logger = logger or logging.getLogger("test_virtual_account_retry_job")
    job = VirtualAccountRetryJob(
        session=session,
        poll_interval=0,
        max_per_batch=max_per_batch,
        max_retries=max_retries,
        logger=logger,
    )

    def _build_service(self, *, redis_client: Any | None = None) -> VirtualAccountService:
        return VirtualAccountService(
            virtual_account_repository=VirtualAccountRepository(session=self.session),
            wallet_repository=WalletRepository(session=self.session),
            user_repository=UserRepository(session=self.session),
            provider_services={"flutterwave": provider_service},
            session=self.session,
            logger=self.logger,
            redis_client=redis_client,
            lock_timeout_seconds=settings.virtual_account_retry_job_lock_timeout_seconds,
            retry_interval_seconds=settings.virtual_account_retry_job_retry_interval_seconds,
            max_retries=self.max_retries,
        )

    job._build_service = _build_service.__get__(job, VirtualAccountRetryJob)
    return job


@pytest.mark.asyncio
async def test_create_virtual_account_masks_account_number_in_logs(caplog):
    wallet_id = uuid4()
    user_id = uuid4()
    account_number = "1234567890"

    service = VirtualAccountService(
        virtual_account_repository=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(),
                    wallet_id=wallet_id,
                    user_id=user_id,
                    provider="flutterwave",
                    account_number=account_number,
                    is_primary=True,
                )
            )
        ),
        wallet_repository=SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(
                    user_id=user_id,
                    is_active=True,
                    is_frozen=False,
                    is_suspended=False,
                    status="active",
                )
            )
        ),
        user_repository=SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=user_id))),
        provider_services={},
        logger=logging.getLogger("virtual_account_mask_test"),
    )
    service._check_duplicate_account = AsyncMock()
    service._resolve_primary_flag = AsyncMock(return_value=True)
    service._clear_other_primary_accounts = AsyncMock()
    service._emit_audit = AsyncMock()

    with caplog.at_level(logging.INFO, logger="virtual_account_mask_test"):
        await service.create_virtual_account(
            wallet_id=wallet_id,
            user_id=user_id,
            provider="flutterwave",
            account_number=account_number,
        )

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert account_number not in rendered
    assert any(record.__dict__.get("account_number") == "******7890" for record in caplog.records)


async def patch_redis(monkeypatch, redis_client: Any | None = None) -> None:
    async def fake_get_redis():
        return redis_client

    monkeypatch.setattr(retry_job_module, "get_redis", fake_get_redis)


def test_successful_provision_runs_once_and_activates_account(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService(
        [
            {
                "account_number": "1234567890",
                "provider_reference": "ref-123",
                "provider_account_id": "acct-123",
                "account_name": "Provisioned Test Account",
                "bank_name": "Flutterwave Bank",
            }
        ]
    )

    job = build_retry_job(test_session, provider_service=provider)
    arun(job.run_once())

    arun(test_session.refresh(account))

    assert provider.calls == 1
    assert account.status == "ACTIVE"
    assert account.provisioned_at is not None
    assert account.retry_count == 0
    assert account.next_retry_at is None
    assert account.last_error is None


def test_provider_timeout_increments_retry_and_continues_processing(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account1 = asyncio.run(create_pending_virtual_account(test_session, wallet, user))
    account2 = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService(
        [
            TimeoutError("provider timed out"),
            {
                "account_number": "2222222222",
                "provider_reference": "ref-222",
                "provider_account_id": "acct-222",
                "account_name": "Success Account",
                "bank_name": "Flutterwave Bank",
            },
        ]
    )

    job = build_retry_job(test_session, provider_service=provider)
    arun(job.run_once())

    arun(test_session.refresh(account1))
    arun(test_session.refresh(account2))

    assert provider.calls == 2
    assert account1.status == "PENDING"
    assert account1.retry_count == 1
    assert account1.last_error == "provider timed out"
    assert account1.last_retry_at is not None
    assert account1.next_retry_at is not None
    assert account1.next_retry_at > account1.last_retry_at
    assert account2.status == "ACTIVE"
    assert account2.provisioned_at is not None


def test_retry_success_clears_schedule_and_preserves_retry_count(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService(
        [
            TimeoutError("temporary failure"),
            {
                "account_number": "3333333333",
                "provider_reference": "ref-333",
                "provider_account_id": "acct-333",
                "account_name": "Recovered Account",
                "bank_name": "Flutterwave Bank",
            },
        ]
    )

    job = build_retry_job(test_session, provider_service=provider, max_retries=5)
    arun(job.run_once())

    arun(test_session.refresh(account))
    assert account.status == "PENDING"
    assert account.retry_count == 1
    assert account.next_retry_at is not None

    account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    test_session.add(account)
    arun(test_session.flush())

    arun(job.run_once())
    arun(test_session.refresh(account))

    assert account.status == "ACTIVE"
    assert account.retry_count == 1
    assert account.provisioned_at is not None
    assert account.next_retry_at is None
    assert account.last_error is None, "A successful provision should clear previous error state"


def test_max_retries_exhaustion_marks_failed_and_logs(monkeypatch, caplog, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user, retry_count=6))

    provider = FakeProviderService(
        [
            {
                "account_number": "4444444444",
                "provider_reference": "ref-444",
                "provider_account_id": "acct-444",
                "account_name": "Should Not Provision",
                "bank_name": "Flutterwave Bank",
            }
        ]
    )

    job = build_retry_job(test_session, provider_service=provider, max_retries=6)
    caplog.set_level(logging.INFO)
    arun(job.run_once())

    arun(test_session.refresh(account))

    assert account.status == "FAILED"
    assert account.next_retry_at is None
    assert account.last_error == "Max retries exceeded"
    assert any(
        record.getMessage().startswith("virtual_account_retry_exhausted")
        or getattr(record, "virtual_account_id", None) == str(account.id)
        for record in caplog.records
    )
    assert provider.calls == 0


def test_restart_recovery_processes_pending_account_once(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService(
        [
            {
                "account_number": "5555555555",
                "provider_reference": "ref-555",
                "provider_account_id": "acct-555",
                "account_name": "Recovery Account",
                "bank_name": "Flutterwave Bank",
            }
        ]
    )

    job1 = build_retry_job(test_session, provider_service=provider)
    arun(job1.run_once())
    arun(test_session.refresh(account))
    assert account.status == "ACTIVE"

    job2 = build_retry_job(test_session, provider_service=provider)
    arun(job2.run_once())
    assert provider.calls == 1


def test_duplicate_worker_protection_uses_distributed_lock(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService(
        [
            asyncio.sleep(0.1, result={
                "account_number": "6666666666",
                "provider_reference": "ref-666",
                "provider_account_id": "acct-666",
                "account_name": "Locked Account",
                "bank_name": "Flutterwave Bank",
            })
        ]
    )
    redis_client = FakeRedis(allow_first_lock=True)

    # Record calls to the redis client's `set` to assert locking semantics
    redis_client.recorded_calls = []
    orig_set = redis_client.set

    async def recording_set(self, key, value, nx=False, ex=None):
        self.recorded_calls.append((key, value, nx, ex))
        return await orig_set(key, value, nx=nx, ex=ex)

    # bind the recorder as the instance method
    recording_set = recording_set.__get__(redis_client, redis_client.__class__)
    redis_client.set = recording_set

    async def fake_get_redis():
        return redis_client

    monkeypatch.setattr(retry_job_module, "get_redis", fake_get_redis)

    job1 = build_retry_job(test_session, provider_service=provider, redis_client=redis_client)
    job2 = build_retry_job(test_session, provider_service=provider, redis_client=redis_client)

    arun_gather(job1.run_once(), job2.run_once())
    # ensure both workers attempted to acquire the same lock and that
    # the first succeeded (nx=True) while the second failed
    assert hasattr(redis_client, "recorded_calls") and len(redis_client.recorded_calls) >= 2
    first_key, first_val, first_nx, first_ex = redis_client.recorded_calls[0]
    second_key, second_val, second_nx, second_ex = redis_client.recorded_calls[1]

    expected_key = f"virtual-account-retry:{account.id}"
    assert first_key == expected_key
    assert second_key == expected_key
    assert first_nx is True
    assert second_nx is True
    # lock timeout should match configured settings
    assert first_ex == settings.virtual_account_retry_job_lock_timeout_seconds
    assert second_ex == settings.virtual_account_retry_job_lock_timeout_seconds

    arun(test_session.refresh(account))
    assert provider.calls == 1
    assert account.status == "ACTIVE"


@pytest.mark.asyncio
async def test_batch_processing_splits_pending_accounts_into_three_runs(monkeypatch, test_session):
    user, wallet = await create_user_and_wallet(test_session)
    accounts = []
    for _ in range(250):
        acct = await create_pending_virtual_account(test_session, wallet, user)
        accounts.append(acct)

    provider = FakeProviderService(
        [
            {
                "account_number": f"batch-{i:06d}",
                "provider_reference": f"ref-batch-{i:06d}",
                "provider_account_id": f"acct-batch-{i:06d}",
                "account_name": "Batch Account",
                "bank_name": "Flutterwave Bank",
            }
            for i in range(250)
        ]
    )

    job = build_retry_job(test_session, provider_service=provider, max_per_batch=100)

    await job.run_once()
    active_count = await test_session.execute(
        text("SELECT COUNT(*) FROM virtual_accounts WHERE status = 'ACTIVE'")
    )
    assert active_count.scalar_one() == 100

    await job.run_once()
    active_count = await test_session.execute(
        text("SELECT COUNT(*) FROM virtual_accounts WHERE status = 'ACTIVE'")
    )
    assert active_count.scalar_one() == 200

    await job.run_once()
    active_count = await test_session.execute(
        text("SELECT COUNT(*) FROM virtual_accounts WHERE status = 'ACTIVE'")
    )
    assert active_count.scalar_one() == 250


def test_exponential_backoff_and_final_failure(monkeypatch, test_session):
    user, wallet = asyncio.run(create_user_and_wallet(test_session))
    account = asyncio.run(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService([
        TimeoutError("timeout 1"),
        TimeoutError("timeout 2"),
        TimeoutError("timeout 3"),
        TimeoutError("timeout 4"),
        TimeoutError("timeout 5"),
    ])

    service = VirtualAccountService(
        virtual_account_repository=VirtualAccountRepository(session=test_session),
        wallet_repository=WalletRepository(session=test_session),
        user_repository=UserRepository(session=test_session),
        provider_services={"flutterwave": provider},
        session=test_session,
        logger=logging.getLogger("test_virtual_account_service"),
        redis_client=None,
        lock_timeout_seconds=60,
        retry_interval_seconds=60,
        max_retries=5,
    )

    deltas = []
    for expected in [60, 120, 240, 480]:
        arun(service.process_retryable_account(virtual_account=account))
        arun(test_session.refresh(account))
        assert account.status == "PENDING"
        delta = account.next_retry_at - account.last_retry_at
        deltas.append(round(delta.total_seconds()))
        assert deltas[-1] == expected

    arun(service.process_retryable_account(virtual_account=account))
    arun(test_session.refresh(account))

    assert account.status == "FAILED"
    assert account.next_retry_at is None


@pytest.mark.asyncio
async def test_retry_logging_includes_required_fields(monkeypatch, caplog, test_session):
    user, wallet = await create_user_and_wallet(test_session)
    account = await create_pending_virtual_account(test_session, wallet, user)

    provider = FakeProviderService(
        [
            TimeoutError("timeout log"),
            {
                "account_number": "7777777777",
                "provider_reference": "ref-777",
                "provider_account_id": "acct-777",
                "account_name": "Logged Account",
                "bank_name": "Flutterwave Bank",
            },
        ]
    )

    job = build_retry_job(test_session, provider_service=provider, max_retries=5)
    caplog.set_level(logging.INFO)

    await job.run_once()
    await test_session.refresh(account)

    assert any(
        getattr(record, "virtual_account_id", None) == str(account.id)
        and getattr(record, "wallet_id", None) == str(wallet.id)
        and getattr(record, "provider", None) == "flutterwave"
        and getattr(record, "retry_count", None) == 1
        and getattr(record, "status", None) == "PENDING"
        and getattr(record, "duration", None) is not None
        and getattr(record, "error", None) == "timeout log"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_graceful_shutdown_finishes_current_account_before_exiting(monkeypatch, test_session):
    user, wallet = await create_user_and_wallet(test_session)
    account = await create_pending_virtual_account(test_session, wallet, user)

    async def delayed_create_virtual_account(customer, metadata):
        await asyncio.sleep(0.1)
        return {
            "account_number": "8888888888",
            "provider_reference": "ref-888",
            "provider_account_id": "acct-888",
            "account_name": "Shutdown Account",
            "bank_name": "Flutterwave Bank",
        }

    provider = SimpleNamespace(create_virtual_account=delayed_create_virtual_account)
    job = build_retry_job(test_session, provider_service=provider, max_retries=3)

    task = asyncio.create_task(job.run_worker())
    await asyncio.sleep(0.01)
    job.stop()
    await task

    await test_session.refresh(account)
    assert account.status == "ACTIVE"
    assert task.done()
