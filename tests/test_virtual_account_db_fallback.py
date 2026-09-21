import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.user_repository import UserRepository
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.services.virtual_account_service import VirtualAccountService
from app.config import settings


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
        if asyncio.iscoroutine(result):
            return await result
        return result


class FakeRedisRaising:
    def __init__(self):
        self.calls = 0

    async def set(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("redis unavailable")

    async def delete(self, *args, **kwargs):
        return 0


class FakeRedisDenied:
    def __init__(self):
        self.calls = 0

    async def set(self, *args, **kwargs):
        self.calls += 1
        return False

    async def delete(self, *args, **kwargs):
        return 0


@pytest.fixture(scope="module")
def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:memdb_dbfallback?mode=memory&cache=shared&uri=true",
        future=True,
        echo=False,
    )

    async def prepare():
        async with engine.begin() as conn:
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


async def create_pending_virtual_account(session: AsyncSession, wallet: Wallet, user: User) -> VirtualAccount:
    account = VirtualAccount(
        id=uuid4(),
        wallet_id=wallet.id,
        user_id=user.id,
        provider="flutterwave",
        account_number=f"PENDING{uuid4().hex[:10]}",
        account_name="Test Account",
        bank_name="Test Bank",
        currency="NGN",
        status="PENDING",
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


def build_retry_job(session: AsyncSession, provider_service: Any, redis_client: Any | None = None, logger: logging.Logger | None = None) -> VirtualAccountRetryJob:
    logger = logger or logging.getLogger("test_virtual_account_retry_job_dbfallback")
    job = VirtualAccountRetryJob(
        session=session,
        poll_interval=0,
        max_per_batch=100,
        max_retries=None,
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


def test_db_fallback_claim_called_when_redis_raises(monkeypatch, test_session):
    # Arrange
    user, wallet = arun(create_user_and_wallet(test_session))
    account = arun(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService([
        {"account_number": "9999999999", "provider_reference": "ref-999", "provider_account_id": "acct-999"}
    ])

    fake_redis = FakeRedisRaising()

    async def fake_get_redis():
        return fake_redis

    # spy for repository claim method
    calls = {"count": 0}

    async def fake_claim(self, virtual_account_id, *, worker_id: str | None = None, lock_ttl_seconds: int):
        calls["count"] += 1
        return True

    monkeypatch.setattr("app.jobs.virtual_account_retry_job.get_redis", fake_get_redis)
    monkeypatch.setattr(VirtualAccountRepository, "claim_retry_for_processing", fake_claim, raising=False)

    job = build_retry_job(test_session, provider_service=provider)

    # Act
    arun(job.run_once())

    # Assert: provider called once and repository claim spy invoked
    assert provider.calls == 1
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_db_fallback_claim_only_one_of_two_workers(monkeypatch, test_session):
    # Arrange
    user, wallet = await create_user_and_wallet(test_session)
    account = await create_pending_virtual_account(test_session, wallet, user)

    provider = FakeProviderService([
        {"account_number": "1212121212", "provider_reference": "ref-121", "provider_account_id": "acct-121"}
    ])

    fake_redis = FakeRedisRaising()

    async def fake_get_redis():
        return fake_redis

    # spy for repository claim method that only allows one successful claim
    state = {"calls": 0}

    async def fake_claim(self, virtual_account_id, *, worker_id: str | None = None, lock_ttl_seconds: int):
        state["calls"] += 1
        return state["calls"] == 1

    monkeypatch.setattr("app.jobs.virtual_account_retry_job.get_redis", fake_get_redis)
    monkeypatch.setattr(VirtualAccountRepository, "claim_retry_for_processing", fake_claim, raising=False)

    job1 = build_retry_job(test_session, provider_service=provider)
    job2 = build_retry_job(test_session, provider_service=provider)

    # Act: run both workers in the same event loop without sharing concurrent SQLAlchemy state
    await job1.run_once()
    await job2.run_once()

    # Assert: provider should be called exactly once and only the first claim should succeed
    assert provider.calls == 1
    assert state["calls"] == 1


def test_redis_lock_denied_does_not_attempt_db_claim(monkeypatch, test_session):
    user, wallet = arun(create_user_and_wallet(test_session))
    account = arun(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService([
        {"account_number": "0000000001", "provider_reference": "ref-000", "provider_account_id": "acct-000"}
    ])
    fake_redis = FakeRedisDenied()

    async def fake_get_redis():
        return fake_redis

    calls = {"count": 0}

    async def fake_claim(self, virtual_account_id, *, worker_id: str | None = None, lock_ttl_seconds: int):
        calls["count"] += 1
        return True

    monkeypatch.setattr("app.jobs.virtual_account_retry_job.get_redis", fake_get_redis)
    monkeypatch.setattr(VirtualAccountRepository, "claim_retry_for_processing", fake_claim, raising=False)

    job = build_retry_job(test_session, provider_service=provider)
    arun(job.run_once())

    assert provider.calls == 0
    assert calls["count"] == 0


def test_db_claim_failure_skips_provider(monkeypatch, test_session):
    user, wallet = arun(create_user_and_wallet(test_session))
    account = arun(create_pending_virtual_account(test_session, wallet, user))

    provider = FakeProviderService([
        {"account_number": "0000000002", "provider_reference": "ref-001", "provider_account_id": "acct-001"}
    ])
    fake_redis = FakeRedisRaising()

    async def fake_get_redis():
        return fake_redis

    calls = {"count": 0}

    async def fake_claim(self, virtual_account_id, *, worker_id: str | None = None, lock_ttl_seconds: int):
        calls["count"] += 1
        return False

    monkeypatch.setattr("app.jobs.virtual_account_retry_job.get_redis", fake_get_redis)
    monkeypatch.setattr(VirtualAccountRepository, "claim_retry_for_processing", fake_claim, raising=False)

    job = build_retry_job(test_session, provider_service=provider)
    arun(job.run_once())

    assert provider.calls == 0
    assert calls["count"] == 1


def test_db_claim_sets_next_retry_at_into_future(test_session):
    user, wallet = arun(create_user_and_wallet(test_session))
    account = arun(create_pending_virtual_account(test_session, wallet, user))

    repository = VirtualAccountRepository(session=test_session)
    claimed = arun(repository.claim_retry_for_processing(account.id, lock_ttl_seconds=45))

    arun(test_session.refresh(account))

    assert claimed is True
    assert account.status == "PROVISIONING"
    assert account.next_retry_at is not None
    assert account.next_retry_at > datetime.now(timezone.utc).replace(tzinfo=None)
