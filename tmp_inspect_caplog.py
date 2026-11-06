import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database.base import Base
from app.models.user import User
from app.models.wallet import Wallet
from app.models.virtual_account import VirtualAccount
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.user_repository import UserRepository
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
    engine = create_async_engine('sqlite+aiosqlite:///file:caplog?mode=memory&cache=shared&uri=true', future=True, echo=False)
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

        provider = FakeProviderService([TimeoutError('timeout log'), {'account_number':'7777777777','provider_reference':'ref-777','provider_account_id':'acct-777','account_name':'Logged Account','bank_name':'Flutterwave Bank'}])
        def build_retry_job(session, provider_service):
            job = VirtualAccountRetryJob(session=session, poll_interval=0, max_per_batch=100, max_retries=5, logger=logging.getLogger('test_virtual_account_retry_job'))
            def _build_service(self, *, redis_client=None):
                from app.repositories.user_repository import UserRepository
                from app.repositories.wallet_repository import WalletRepository
                repository = VirtualAccountRepository(session=self.session)
                wallet_repository = WalletRepository(session=self.session)
                user_repository = UserRepository(session=self.session)
                service = VirtualAccountService(virtual_account_repository=repository, wallet_repository=wallet_repository, user_repository=user_repository, provider_services={'flutterwave':provider}, session=self.session, logger=self.logger, redis_client=None, lock_timeout_seconds=60, retry_interval_seconds=60, max_retries=5)
                service.redis_unavailable = True
                return service
            job._build_service = _build_service.__get__(job, VirtualAccountRetryJob)
            return job
        job = build_retry_job(session, provider)

        records = []
        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)
        handler = CaptureHandler()
        handler.setLevel(logging.INFO)
        job.logger.addHandler(handler)
        job.logger.setLevel(logging.INFO)

        await job.run_once()
        await session.refresh(account)
        print('status', account.status, 'retry_count', account.retry_count, 'next_retry_at', account.next_retry_at)
        print('provider.calls', provider.calls)
        print('records', len(records))
        for r in records:
            print('--- record ---')
            print('msg', r.getMessage())
            print('name', r.name)
            print('level', r.levelname)
            print('attrs', {k:v for k,v in r.__dict__.items() if k not in ['msg','args','levelname','levelno','pathname','filename','module','exc_info','stack_info','lineno','funcName','created','msecs','relativeCreated','thread','threadName','processName','process']})
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
