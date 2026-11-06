import asyncio
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository

async def main():
    engine = create_async_engine(
        'sqlite+aiosqlite:///file:memdb_testclaim?mode=memory&cache=shared&uri=true',
        future=True,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with async_session() as session:
        user = User(id=uuid4(), email='u@example.com', username='u', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        account = VirtualAccount(
            id=uuid4(),
            wallet_id=wallet.id,
            user_id=user.id,
            provider='flutterwave',
            account_number='PENDING123',
            account_name='Test',
            bank_name='Bank',
            currency='NGN',
            status='PENDING',
        )
        session.add_all([user, wallet, account])
        await session.flush()
        await session.refresh(account)
        print('before', account.id, account.status, account.next_retry_at)
        repo = VirtualAccountRepository(session=session)
        try:
            ok = await repo.claim_retry_for_processing(account.id, lock_ttl_seconds=10)
            print('claim ok', ok)
        except Exception as exc:
            print('claim exception', type(exc).__name__, exc)
        finally:
            try:
                await session.refresh(account)
                print('after refresh', account.status, account.next_retry_at)
            except Exception as exc:
                print('post-refresh exception', type(exc).__name__, exc)
            result = await session.execute('SELECT status, next_retry_at FROM virtual_accounts WHERE id = :id', {'id': str(account.id)})
            print('direct', result.all())
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
