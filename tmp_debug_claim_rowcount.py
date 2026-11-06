import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_claimrow?mode=memory&cache=shared&uri=true', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with async_session() as session:
        user = User(id=uuid4(), email='u@example.com', username='u', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        account = VirtualAccount(id=uuid4(), wallet_id=wallet.id, user_id=user.id, provider='flutterwave', account_number='PENDING123', account_name='Test', bank_name='Bank', currency='NGN', status='PENDING')
        session.add_all([user, wallet, account])
        await session.flush()
        await session.refresh(account)
        print('initial', account.status, account.next_retry_at)

        repo = VirtualAccountRepository(session=session)
        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()
        await session.refresh(account)
        print('before claim', account.status, account.next_retry_at)

        # call claim directly and inspect rowcount if possible
        from sqlalchemy import update
        from sqlalchemy import select
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        claim_until = now + timedelta(seconds=60)
        stmt = update(VirtualAccount).where(VirtualAccount.id == account.id, VirtualAccount.status == 'PENDING', (VirtualAccount.next_retry_at.is_(None) | (VirtualAccount.next_retry_at <= now))).values(status='PROVISIONING', next_retry_at=claim_until)
        result = await session.execute(stmt)
        print('direct execute rowcount', result.rowcount)
        await session.commit()
        row = (await session.execute(select(VirtualAccount).where(VirtualAccount.id == account.id))).scalar_one()
        print('row after direct execute', row.status, row.next_retry_at)

        # Reset and try using repository method with same session
        account.status = 'PENDING'
        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()
        await session.refresh(account)
        print('reset before repo claim', account.status, account.next_retry_at)

        result = await repo.claim_retry_for_processing(account.id, lock_ttl_seconds=60)
        print('repo claim returned', result)
        await session.refresh(account)
        print('after repo claim', account.status, account.next_retry_at)
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
