import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.services.virtual_account_service import VirtualAccountService
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.config import settings

class FakeProviderService:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def create_virtual_account(self, customer, metadata):
        self.calls += 1
        result = self.responses[self.calls - 1]
        if isinstance(result, Exception):
            raise result
        if inspect.isawaitable(result):
            return await result
        return result

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_debugservice?mode=memory&cache=shared&uri=true', future=True, echo=False)
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
        print('initial', account.id, account.status, account.retry_count, account.next_retry_at)

        provider = FakeProviderService([
            TimeoutError('temporary failure'),
            {
                'account_number': '3333333333',
                'provider_reference': 'ref-333',
                'provider_account_id': 'acct-333',
                'account_name': 'Recovered Account',
                'bank_name': 'Flutterwave Bank',
            },
        ])

        service = VirtualAccountService(
            virtual_account_repository=VirtualAccountRepository(session=session),
            wallet_repository=WalletRepository(session=session),
            user_repository=UserRepository(session=session),
            provider_services={'flutterwave': provider},
            session=session,
            logger=logging.getLogger('debugservice'),
            redis_client=None,
            lock_timeout_seconds=settings.virtual_account_retry_job_lock_timeout_seconds,
            retry_interval_seconds=settings.virtual_account_retry_job_retry_interval_seconds,
            max_retries=5,
        )
        service.redis_unavailable = True

        print('first process')
        result1 = await service.process_retryable_account(virtual_account=account, max_retries=5)
        await session.refresh(result1)
        print('result1', result1.status, result1.retry_count, result1.last_error, result1.next_retry_at)
        print('account after result1', account.status, account.retry_count, account.last_error, account.next_retry_at)

        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()
        await session.refresh(account)
        print('rewound account next_retry_at', account.status, account.next_retry_at)

        print('second process')
        try:
            result2 = await service.process_retryable_account(virtual_account=account, max_retries=5)
            await session.refresh(result2)
            print('result2', result2.status, result2.retry_count, result2.last_error, result2.next_retry_at, result2.provisioned_at)
        except Exception as exc:
            print('process exception', type(exc).__name__, exc)
        print('after second process account', account.status, account.retry_count, account.last_error, account.next_retry_at, account.provisioned_at)

        row = await session.execute(select(VirtualAccount).where(VirtualAccount.id == account.id))
        row = row.scalar_one()
        print('DB row', row.status, row.retry_count, row.next_retry_at, row.last_error, row.provisioned_at)
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
