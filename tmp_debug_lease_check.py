import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet
from app.repositories.virtual_account_repository import VirtualAccountRepository


async def main():
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
        user = User(id=uuid4(), email="u@e.com", username="u", status="active", is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency="NGN", status="active", available_balance=0, ledger_balance=0, locked_balance=0)
        session.add_all([user, wallet])
        await session.flush()
        account = VirtualAccount(
            id=uuid4(),
            wallet_id=wallet.id,
            user_id=user.id,
            provider="flutterwave",
            account_number="LEAS123",
            account_name="Lease Test",
            bank_name="Always Bank",
            currency="NGN",
            status="PENDING",
            retry_count=0,
            last_retry_at=None,
            next_retry_at=datetime.now(timezone.utc),
            last_error=None,
            provisioned_at=None,
        )
        session.add(account)
        await session.flush()

        repo = VirtualAccountRepository(session)
        print("claim_A", await repo.claim_retry_for_processing(account.id, worker_id="worker-a", lock_ttl_seconds=60))
        lease = await session.get(VirtualAccount, account.id)
        print("lease_before", lease.retry_owner_id, lease.retry_lease_expires_at)
        lease.retry_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        print("row_after_expiry", (await session.get(VirtualAccount, account.id)).retry_owner_id, (await session.get(VirtualAccount, account.id)).retry_lease_expires_at)
        print("claim_B", await repo.claim_retry_for_processing(account.id, worker_id="worker-b", lock_ttl_seconds=60))
        refreshed = await session.get(VirtualAccount, account.id)
        print("after", refreshed.retry_owner_id, refreshed.retry_lease_expires_at, refreshed.next_retry_at, refreshed.status)
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
        await engine.dispose()

asyncio.run(main())
