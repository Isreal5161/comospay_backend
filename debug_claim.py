import asyncio
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_debug?mode=memory&cache=shared&uri=true', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        user = User(id=uuid4(), email='u@example.com', username='user1', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        session.add_all([user, wallet])
        await session.flush()
        await session.refresh(user)
        await session.refresh(wallet)
        account = VirtualAccount(id=uuid4(), wallet_id=wallet.id, user_id=user.id, provider='flutterwave', account_number='PENDING123', account_name='Test Account', bank_name='Test Bank', currency='NGN', status='PENDING', retry_count=0, last_retry_at=None, next_retry_at=None, last_error=None, provisioned_at=None, metadata_payload={})
        session.add(account)
        await session.flush()
        await session.refresh(account)
        repository = VirtualAccountRepository(session=session)
        print('account id type', type(account.id), account.id)
        claimed = await repository.claim_retry_for_processing(account.id, lock_ttl_seconds=45)
        print('claimed', claimed)
        await session.refresh(account)
        print('status', account.status, 'next_retry_at', account.next_retry_at)

asyncio.run(main())
