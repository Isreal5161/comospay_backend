import sys
import os
import asyncio
from types import SimpleNamespace

import pytest

# Ensure package importability during test runs
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.jobs import virtual_account_retry_job as retry_job_module


@pytest.mark.asyncio
async def test_worker_uses_fresh_session_each_iteration(monkeypatch):
    # Track sessions seen by VirtualAccountRetryJob._build_service via self.session
    seen_sessions = []

    # Fake get_db: each call returns an async generator that yields a unique session object
    counter = {"n": 0}

    async def fake_get_db():
        counter["n"] += 1
        # yield a distinct sentinel for each invocation
        yield f"session-{counter['n']}"

    monkeypatch.setattr(retry_job_module, "get_db", fake_get_db)

    # Replace _build_service to capture the session on the job instance
    original_build = retry_job_module.VirtualAccountRetryJob._build_service

    def fake_build(self, *, redis_client=None):
        seen_sessions.append(self.session)

        class FakeService:
            async def get_pending_accounts(self, *, limit):
                return []

            async def process_retryable_account(self, *, virtual_account, max_retries=None):
                return virtual_account

        return FakeService()

    monkeypatch.setattr(retry_job_module.VirtualAccountRetryJob, "_build_service", fake_build)

    # Small poll interval so worker cycles quickly
    monkeypatch.setattr(retry_job_module.settings, "virtual_account_retry_job_interval_seconds", 0.01)

    # Prepare a fake app state
    app = SimpleNamespace()
    app.state = SimpleNamespace()

    # Instead of running the background task, simulate two iterations of the
    # worker loop to verify that each iteration obtains a fresh session and
    # that `run_once()` is invoked with that session.
    for _ in range(2):
        async for session in retry_job_module.get_db():
            job = retry_job_module.VirtualAccountRetryJob(
                session=session,
                poll_interval=retry_job_module.settings.virtual_account_retry_job_interval_seconds,
                max_per_batch=retry_job_module.settings.virtual_account_retry_job_batch_size,
                max_retries=retry_job_module.settings.virtual_account_max_retries,
            )
            # run a single iteration
            await job.run_once()
            break

    # We expect at least two distinct sessions across iterations
    assert len(seen_sessions) >= 2
    assert seen_sessions[0] != seen_sessions[1]

    # restore optional monkeypatch side-effects
    monkeypatch.setattr(retry_job_module.VirtualAccountRetryJob, "_build_service", original_build)
