import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.services.virtual_account_service import VirtualAccountService

class FakeProviderService:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def create_virtual_account(self, customer, metadata):
        self.calls += 1
        result = self.responses[self.calls - 1]
        if isinstance(result, Exception):
            raise result
        return result

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:claim_exception?mode=memory&cache=shared&uri=true', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        user = User(id=uuid4(), email='u@example.com', username='u', status='active', is_active=True)
        wallet = Wallet(id=uuid4(), user_id=user.id, currency='NGN', status='active', available_balance=0, ledger_balance=0, locked_balance=0)
        account = VirtualAccount(id=uuid4(), wallet_id=wallet.id, user_id=user.id, provider='flutterwave', account_number='pending', account_name='Test', bank_name='Bank', currency='NGN', status='PENDING')
        session.add_all([user, wallet, account])
        await session.flush()
        await session.refresh(account)

        provider = FakeProviderService([
            TimeoutError('temporary failure'),
            {
                'account_number':'3333333333',
                'provider_reference':'ref-333',
                'provider_account_id':'acct-333',
                'account_name':'Recovered Account',
                'bank_name':'Flutterwave Bank',
            }
        ])

        job = VirtualAccountRetryJob(session=session, poll_interval=0, max_per_batch=100, max_retries=5, logger=logging.getLogger('test'))

        def _build_service(self, *, redis_client=None):
            repository = VirtualAccountRepository(session=self.session)
            wallet_repository = WalletRepository(session=self.session)
            user_repository = UserRepository(session=self.session)
            service = VirtualAccountService(
                virtual_account_repository=repository,
                wallet_repository=wallet_repository,
                user_repository=user_repository,
                provider_services={'flutterwave': provider},
                session=self.session,
                logger=self.logger,
                redis_client=None,
                lock_timeout_seconds=60,
                retry_interval_seconds=60,
                max_retries=self.max_retries,
            )
            service.redis_unavailable = True
            return service

        job._build_service = _build_service.__get__(job, VirtualAccountRetryJob)

        original_claim = VirtualAccountRepository.claim_retry_for_processing

        async def wrapped_claim(self, virtual_account_id, *, lock_ttl_seconds):
            print('wrapped attempt before claim', virtual_account_id)
            try:
                result = await original_claim(self, virtual_account_id, lock_ttl_seconds=lock_ttl_seconds)
                print('wrapped attempt result', result)
                return result
            except Exception as exc:
                print('wrapped attempt exception', type(exc).__name__, exc)
                raise

        VirtualAccountRepository.claim_retry_for_processing = wrapped_claim

        await job.run_once()
        await session.refresh(account)
        print('after first run', account.status, account.retry_count, account.next_retry_at, provider.calls)

        account.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(account)
        await session.flush()

        await job.run_once()
        await session.refresh(account)
        print('after second run', account.status, account.retry_count, account.next_retry_at, provider.calls)
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
