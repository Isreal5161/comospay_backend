import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database.base import Base
from app.jobs.virtual_account_retry_job import VirtualAccountRetryJob
from app.repositories.virtual_account_repository import VirtualAccountRepository
from tests.test_virtual_account_provisioning_integration import FakeProviderService, create_user_and_wallet, create_pending_virtual_account, build_retry_job

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = await session_factory().__aenter__()
    try:
        user, wallet = await create_user_and_wallet(session)
        account = await create_pending_virtual_account(session, wallet, user)
        repo = VirtualAccountRepository(session)
        print('before count', len(await repo.list_retryable_accounts(limit=10)))
        provider = FakeProviderService([{'account_number':'123','provider_reference':'ref','provider_account_id':'acct','account_name':'x','bank_name':'y'}])
        job = build_retry_job(session, provider_service=provider)
        await job.run_once()
        await session.refresh(account)
        print('status', account.status)
        print('retry_count', account.retry_count)
        print('next_retry_at', account.next_retry_at)
        print('last_error', account.last_error)
        print('provider_calls', provider.calls)
        print('after count', len(await repo.list_retryable_accounts(limit=10)))
    finally:
        await session.__aexit__(None, None, None)
        await engine.dispose()

asyncio.run(main())
