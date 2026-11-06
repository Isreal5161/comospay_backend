import asyncio
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_repro?mode=memory&cache=shared&uri=true', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        user = User(id=uuid4(), email='u@example.com', username='u', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        account = VirtualAccount(id=uuid4(), wallet_id=wallet.id, user_id=user.id, provider='flutterwave', account_number='123', status='PENDING')
        session.add_all([user, wallet, account])
        await session.flush()
        await session.commit()
        repo = VirtualAccountRepository(session=session)
        print('before', account.status, account.next_retry_at)
        try:
            claimed = await repo.claim_retry_for_processing(account.id, lock_ttl_seconds=45)
            print('claimed', claimed)
            await session.refresh(account)
            print('after', account.status, account.next_retry_at)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            await session.rollback()
    await engine.dispose()

asyncio.run(main())
