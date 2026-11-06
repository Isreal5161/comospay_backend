import asyncio
import logging
from uuid import uuid4
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.virtual_account_service import VirtualAccountService

class FakeProviderService:
    async def create_virtual_account(self, customer, metadata):
        return {'account_number':'1','provider_reference':'ref','provider_account_id':'acct'}

class FakeRedisRaising:
    async def set(self, *args, **kwargs):
        raise RuntimeError('redis unavailable')
    async def delete(self, *args, **kwargs):
        return 0

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_debugclaim?mode=memory&cache=shared&uri=true', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        user = User(id=uuid4(), email='u@example.com', username='test', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        session.add_all([user, wallet]); await session.flush(); await session.refresh(user); await session.refresh(wallet)
        account = VirtualAccount(id=uuid4(), wallet_id=wallet.id, user_id=user.id, provider='flutterwave', account_number='PENDING123', account_name='Test Account', bank_name='Test Bank', currency='NGN', status='PENDING')
        session.add(account); await session.flush(); await session.refresh(account)
        repo = VirtualAccountRepository(session=session)
        service = VirtualAccountService(
            virtual_account_repository=repo,
            wallet_repository=WalletRepository(session=session),
            user_repository=UserRepository(session=session),
            provider_services={'flutterwave': FakeProviderService()},
            session=session,
            logger=logging.getLogger('debug'),
            redis_client=FakeRedisRaising(),
            lock_timeout_seconds=60,
            retry_interval_seconds=60,
            max_retries=5,
        )
        result = await service._acquire_retry_lock(account)
        print('acquire', result)
        try:
            result = await service._attempt_db_claim(account)
            print('claim result', result)
        except Exception as exc:
            print('claim exception', type(exc), exc)
            import traceback; traceback.print_exc()

asyncio.run(main())
