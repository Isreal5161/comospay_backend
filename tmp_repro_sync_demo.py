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
        print("initial account state", account.retry_owner_id)

        now = datetime.now(timezone.utc)
        claim_until = now + timedelta(seconds=60)
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
            .execution_options(synchronize_session="fetch")
        )
        await session.commit()
        print("rowcount", result.rowcount)
        print("account after commit", account.retry_owner_id)
        print("session.get", (await session.get(VirtualAccount, account.id)).retry_owner_id)
        row = (await session.execute(select(VirtualAccount).where(VirtualAccount.id == account.id))).scalar_one()
        print("fresh row", row.retry_owner_id)
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
        await engine.dispose()

asyncio.run(main())
