import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.user import User
from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet


async def main():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = await session_factory().__aenter__()
    try:
        user = User(id=uuid4(), email="u@example.com", username="u", status="active", is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency="NGN", status="active", available_balance=0, ledger_balance=0, locked_balance=0)
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
            next_retry_at=datetime.now(timezone.utc),
        )
        session.add_all([user, wallet, account])
        await session.flush()

        now = datetime.now(timezone.utc)
        claim_until = now + timedelta(seconds=60)
        print("BEFORE", account.retry_owner_id, account.status)
        result = await session.execute(
            update(VirtualAccount)
            .where(VirtualAccount.id == account.id)
            .values(
                status="PROVISIONING",
                retry_owner_id="worker-a",
                retry_claimed_at=now,
                retry_lease_expires_at=claim_until,
                next_retry_at=claim_until,
            )
        )
        await session.commit()
        print("ROWCOUNT", result.rowcount)
        print("OBJECT AFTER COMMIT", account.retry_owner_id, account.status)
        query = await session.execute(select(VirtualAccount).where(VirtualAccount.id == account.id))
        row = query.scalar_one()
        print("FRESH ROW", row.retry_owner_id, row.status, row.retry_lease_expires_at)

        session.expire_all()
        reloaded = await session.get(VirtualAccount, account.id)
        print("AFTER EXPIRE_ALL GET", reloaded.retry_owner_id, reloaded.status)
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
        await engine.dispose()

asyncio.run(main())
