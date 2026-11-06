import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.services.virtual_account_service import VirtualAccountService
from app.config import settings

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_claim?mode=memory&cache=shared&uri=true', future=True, echo=False)
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
            account_number=f'PENDING{uuid4().hex[:10]}',
            account_name='Test Account',
            bank_name='Test Bank',
            currency='NGN',
            status='PENDING',
        )
        session.add_all([user, wallet, account])
        await session.flush()
        await session.refresh(account)

        service = VirtualAccountService(
            virtual_account_repository=VirtualAccountRepository(session=session),
            wallet_repository=WalletRepository(session=session),
            user_repository=UserRepository(session=session),
            provider_services={},
            session=session,
            redis_client=None,
            lock_timeout_seconds=60,
            retry_interval_seconds=60,
            max_retries=5,
        )
        service.redis_unavailable = True

        # Manually test DB claim on a clean pending account
        try:
            ok = await service._attempt_db_claim(account)
            print('attempt_db_claim ok', ok)
        except Exception as exc:
            print('attempt_db_claim exception', type(exc).__name__, exc)

        await session.refresh(account)
        print('after claim', account.status, account.next_retry_at)

        # Reset account to PENDING and simulate second claim after explicit update
        account.status = 'PENDING'
        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()

        try:
            ok2 = await service._attempt_db_claim(account)
            print('attempt_db_claim ok2', ok2)
        except Exception as exc:
            print('attempt_db_claim exception2', type(exc).__name__, exc)
        await session.refresh(account)
        print('after claim2', account.status, account.next_retry_at)

    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
