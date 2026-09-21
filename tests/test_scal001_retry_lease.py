import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet
from app.repositories.user_repository import UserRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.virtual_account_service import VirtualAccountService


@pytest_asyncio.fixture
async def lease_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = await session_factory().__aenter__()
    try:
        yield session
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
        await engine.dispose()


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
    return user, wallet


async def create_retry_account(session: AsyncSession, wallet: Wallet, user: User, *, status: str = "PENDING") -> VirtualAccount:
    account = VirtualAccount(
        id=uuid4(),
        wallet_id=wallet.id,
        user_id=user.id,
        provider="flutterwave",
        account_number=f"LEAS{uuid4().hex[:10]}",
        account_name="Lease Test",
        bank_name="Always Bank",
        currency="NGN",
        status=status,
        retry_count=0,
        last_retry_at=None,
        next_retry_at=datetime.now(timezone.utc),
        last_error=None,
        provisioned_at=None,
    )
    session.add(account)
    await session.flush()
    return account


@pytest.mark.asyncio
async def test_retry_lease_is_exclusive_and_expired_lease_can_be_reclaimed(lease_session):
    user, wallet = await create_user_and_wallet(lease_session)
    account = await create_retry_account(lease_session, wallet, user)
    repository = VirtualAccountRepository(session=lease_session)

    claimed_a = await repository.claim_retry_for_processing(account.id, worker_id="worker-a", lock_ttl_seconds=60)
    assert claimed_a is True

    claimed_b = await repository.claim_retry_for_processing(account.id, worker_id="worker-b", lock_ttl_seconds=60)
    assert claimed_b is False

    lease = await lease_session.get(VirtualAccount, account.id)
    assert lease.retry_owner_id == "worker-a"
    assert lease.retry_lease_expires_at is not None
    assert lease.retry_claimed_at is not None

    lease.retry_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await lease_session.commit()

    claimed_c = await repository.claim_retry_for_processing(account.id, worker_id="worker-b", lock_ttl_seconds=60)
    assert claimed_c is True

    refreshed = await lease_session.get(VirtualAccount, account.id)
    assert refreshed.retry_owner_id == "worker-b"


@pytest.mark.asyncio
async def test_service_rejects_provider_call_after_lease_loss(lease_session):
    user, wallet = await create_user_and_wallet(lease_session)
    account = await create_retry_account(lease_session, wallet, user)

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def create_virtual_account(self, customer: dict, metadata: dict):
            self.calls += 1
            return {
                "account_number": "1234567890",
                "provider_reference": "provider-ref",
                "provider_account_id": "acct-1",
                "account_name": "Account 1",
                "bank_name": "Bank 1",
            }

    provider = FakeProvider()
    service = VirtualAccountService(
        virtual_account_repository=VirtualAccountRepository(session=lease_session),
        wallet_repository=WalletRepository(session=lease_session),
        user_repository=UserRepository(session=lease_session),
        provider_services={"flutterwave": provider},
        session=lease_session,
        logger=None,
        redis_client=None,
        lock_timeout_seconds=60,
        retry_interval_seconds=60,
        max_retries=3,
        worker_id="worker-a",
    )

    await service.virtual_account_repository.claim_retry_for_processing(account.id, worker_id="worker-a", lock_ttl_seconds=60)
    lease = await lease_session.get(VirtualAccount, account.id)
    lease.retry_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    lease.retry_owner_id = "worker-a"
    await lease_session.commit()

    stale = await service.virtual_account_repository.verify_retry_lease(account.id, "worker-a")
    assert stale is False

    result = await service.provision_existing_account(virtual_account=account)
    assert result.retry_owner_id == "worker-a"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_retry_state_clears_lease_after_successful_processing(lease_session):
    user, wallet = await create_user_and_wallet(lease_session)
    account = await create_retry_account(lease_session, wallet, user, status="PROVISIONING")

    class FakeProvider:
        async def create_virtual_account(self, customer: dict, metadata: dict):
            return {
                "account_number": "2020202020",
                "provider_reference": "provider-ref-2",
                "provider_account_id": "acct-2",
                "account_name": "Account 2",
                "bank_name": "Bank 2",
            }

    repository = VirtualAccountRepository(session=lease_session)
    service = VirtualAccountService(
        virtual_account_repository=repository,
        wallet_repository=WalletRepository(session=lease_session),
        user_repository=UserRepository(session=lease_session),
        provider_services={"flutterwave": FakeProvider()},
        session=lease_session,
        logger=None,
        redis_client=None,
        lock_timeout_seconds=60,
        retry_interval_seconds=60,
        max_retries=3,
        worker_id="worker-a",
    )

    assert await repository.claim_retry_for_processing(account.id, worker_id="worker-a", lock_ttl_seconds=60) is True
    updated = await service.provision_existing_account(virtual_account=account)
    assert updated.status == "ACTIVE"
    assert updated.retry_owner_id is None
    assert updated.retry_claimed_at is None
    assert updated.retry_lease_expires_at is None


@pytest.mark.asyncio
async def test_retry_state_clears_lease_after_failed_processing(lease_session):
    user, wallet = await create_user_and_wallet(lease_session)
    account = await create_retry_account(lease_session, wallet, user, status="PROVISIONING")

    class FakeProvider:
        async def create_virtual_account(self, customer: dict, metadata: dict):
            raise RuntimeError("provider failure")

    repository = VirtualAccountRepository(session=lease_session)
    service = VirtualAccountService(
        virtual_account_repository=repository,
        wallet_repository=WalletRepository(session=lease_session),
        user_repository=UserRepository(session=lease_session),
        provider_services={"flutterwave": FakeProvider()},
        session=lease_session,
        logger=None,
        redis_client=None,
        lock_timeout_seconds=60,
        retry_interval_seconds=60,
        max_retries=3,
        worker_id="worker-a",
    )

    assert await repository.claim_retry_for_processing(account.id, worker_id="worker-a", lock_ttl_seconds=60) is True
    updated = await service.provision_existing_account(virtual_account=account)
    assert updated.status in {"PENDING", "FAILED"}
    assert updated.retry_owner_id is None
    assert updated.retry_claimed_at is None
    assert updated.retry_lease_expires_at is None
    assert updated.retry_count >= 1
