import asyncio
import inspect
import logging
from uuid import uuid4
from datetime import datetime
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
    engine = create_async_engine(
        'sqlite+aiosqlite:///file:memdb1?mode=memory&cache=shared&uri=true',
        future=True,
        echo=False,
    )
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
        account = VirtualAccount(
            id=uuid4(),
            wallet_id=wallet.id,
            user_id=user.id,
            provider='flutterwave',
            account_number='PENDING123',
            account_name='Test Account',
            bank_name='Test Bank',
            currency='NGN',
            status='PENDING',
            retry_count=0,
            last_retry_at=None,
            next_retry_at=None,
            last_error=None,
            provisioned_at=None,
            metadata_payload={},
        )
        session.add(account)
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

        def _build_service(self, *, redis_client=None):
            return VirtualAccountService(
                virtual_account_repository=VirtualAccountRepository(session=self.session),
                wallet_repository=WalletRepository(session=self.session),
                user_repository=UserRepository(session=self.session),
                provider_services={'flutterwave': provider},
                session=self.session,
                logger=self.logger,
                redis_client=redis_client,
                lock_timeout_seconds=60,
                retry_interval_seconds=60,
                max_retries=5,
            )

        job = VirtualAccountRetryJob(session=session, poll_interval=0, max_per_batch=100, max_retries=5, logger=logging.getLogger('test'))
        job._build_service = _build_service.__get__(job, VirtualAccountRetryJob)

        await job.run_once()
        await session.refresh(account)

        print('status', account.status)
        print('retry_count', account.retry_count)
        print('next_retry_at', account.next_retry_at)
        print('last_error', account.last_error)
        print('provider.calls', provider.calls)

asyncio.run(main())
