import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.services.virtual_account_service import VirtualAccountService
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
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_debug_claim?mode=memory&cache=shared&uri=true', future=True, echo=False)
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

        job = VirtualAccountRetryJob(session=session, poll_interval=0, max_per_batch=100, max_retries=5, logger=logging.getLogger('inspect'))
        def _build_service(self, *, redis_client=None):
            return VirtualAccountService(
                virtual_account_repository=VirtualAccountRepository(session=self.session),
                wallet_repository=WalletRepository(session=self.session),
                user_repository=UserRepository(session=self.session),
                provider_services={'flutterwave': provider},
                session=self.session,
                logger=self.logger,
                redis_client=redis_client,
                lock_timeout_seconds=settings.virtual_account_retry_job_lock_timeout_seconds,
                retry_interval_seconds=settings.virtual_account_retry_job_retry_interval_seconds,
                max_retries=self.max_retries,
            )
        job._build_service = _build_service.__get__(job, VirtualAccountRetryJob)

        async def fake_get_redis():
            raise RuntimeError('redis unavailable')
        import app.jobs.virtual_account_retry_job as retry_job_module
        retry_job_module.get_redis = fake_get_redis

        original_attempt = VirtualAccountService._attempt_db_claim
        async def wrapped_attempt(self, virtual_account):
            try:
                result = await original_attempt(self, virtual_account)
                print('wrapped attempt result', result)
                return result
            except Exception as exc:
                print('wrapped attempt exception', type(exc).__name__, exc)
                raise
        VirtualAccountService._attempt_db_claim = wrapped_attempt

        print('first run')
        await job.run_once()
        await session.refresh(account)
        print('after first run', account.status, account.retry_count, account.last_error, account.next_retry_at)

        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()

        print('second run')
        await job.run_once()
        await session.refresh(account)
        print('after second run', account.status, account.retry_count, account.last_error, account.next_retry_at)

    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
