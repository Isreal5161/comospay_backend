import asyncio
import inspect
import logging
from datetime import datetime, timezone
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

class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records = []
    def emit(self, record):
        self.records.append(record)

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///file:memdb_log?mode=memory&cache=shared&uri=true', future=True, echo=False)
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
            TimeoutError('timeout log'),
            {
                'account_number': '7777777777',
                'provider_reference': 'ref-777',
                'provider_account_id': 'acct-777',
                'account_name': 'Logged Account',
                'bank_name': 'Flutterwave Bank',
            },
        ])

        log = logging.getLogger('test_log_capture')
        handler = ListHandler()
        log.addHandler(handler)
        log.setLevel(logging.INFO)

        service = VirtualAccountService(
            virtual_account_repository=VirtualAccountRepository(session=session),
            wallet_repository=WalletRepository(session=session),
            user_repository=UserRepository(session=session),
            provider_services={'flutterwave': provider},
            session=session,
            logger=log,
            redis_client=None,
            lock_timeout_seconds=settings.virtual_account_retry_job_lock_timeout_seconds,
            retry_interval_seconds=settings.virtual_account_retry_job_retry_interval_seconds,
            max_retries=5,
        )
        service.redis_unavailable = True

        await service.process_retryable_account(virtual_account=account, max_retries=5)
        await session.refresh(account)
        print('account after', account.status, account.retry_count, account.last_error, account.next_retry_at)
        for rec in handler.records:
            print('record', rec.name, rec.levelname, rec.getMessage())
            print('  attrs:', {k: v for k, v in rec.__dict__.items() if k not in ('name','msg','args','levelname','levelno','pathname','filename','module','exc_info','exc_text','stack_info','lineno','funcName','created','msecs','relativeCreated','thread','threadName','processName','process')})

    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
