from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.jobs import virtual_account_retry_job as retry_job_module


class FakeRedis:
    def __init__(self) -> None:
        self.acquired = []

    async def set(self, key, value, nx=False, ex=None):
        self.acquired.append((key, value, nx, ex))
        return True

    async def delete(self, key):
        self.acquired.append(("delete", key))
        return 1


@pytest.mark.asyncio
async def test_run_once_uses_service_and_skips_repository(monkeypatch):
    account = SimpleNamespace(
        id=uuid4(),
        wallet_id=uuid4(),
        provider="flutterwave",
        retry_count=1,
        status="PENDING",
        next_retry_at=None,
    )
    processed_accounts = []

    class FakeService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def get_pending_accounts(self, *, limit):
            return [account]

        async def process_retryable_account(self, *, virtual_account, max_retries=None):
            processed_accounts.append((virtual_account.id, virtual_account.retry_count))
            return virtual_account

    monkeypatch.setattr(retry_job_module, "get_redis", lambda: FakeRedis())

    def fake_build_service(self, *, redis_client=None):
        return FakeService(redis_client=redis_client)

    monkeypatch.setattr(retry_job_module.VirtualAccountRetryJob, "_build_service", fake_build_service)

    job = retry_job_module.VirtualAccountRetryJob(session=object(), poll_interval=0, max_per_batch=1, max_retries=3)
    await job.run_once()

    assert processed_accounts == [(account.id, 1)]
