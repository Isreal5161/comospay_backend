from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.config.redis import get_redis
from app.config.settings import settings
from app.integrations.payments.flutterwave.client import FlutterwaveClient
from app.integrations.payments.flutterwave.virtual_accounts import FlutterwaveVirtualAccountService
from app.services.virtual_account_service import VirtualAccountService


class VirtualAccountRetryJob:
    """Background job that retries provisioning of pending virtual accounts."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        poll_interval: int | None = None,
        max_per_batch: int | None = None,
        max_retries: int | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session = session
        self._stopped = False
        self.poll_interval = poll_interval if poll_interval is not None else settings.virtual_account_retry_job_interval_seconds
        self.max_per_batch = max_per_batch if max_per_batch is not None else settings.virtual_account_retry_job_batch_size
        self.max_retries = max_retries if max_retries is not None else settings.virtual_account_max_retries
        self.logger = logger or logging.getLogger(__name__)

    async def run_once(self) -> None:
        redis_client = None
        redis_unavailable = False
        try:
            redis_client = await get_redis()
        except Exception as exc:
            self.logger.warning("virtual_account_retry_worker_redis_unavailable", extra={"error": str(exc)})
            redis_unavailable = True

        service = self._build_service(redis_client=redis_client)
        if redis_unavailable and hasattr(service, "redis_unavailable"):
            service.redis_unavailable = True
        pending_accounts = await service.get_pending_accounts(limit=self.max_per_batch)
        if not pending_accounts:
            return

        for account in pending_accounts:
            try:
                await service.process_retryable_account(virtual_account=account, max_retries=self.max_retries)
            except Exception as exc:
                self.logger.exception(
                    "virtual_account_retry_account_failed",
                    extra={
                        "wallet_id": self._snapshot_account_value(account, "wallet_id"),
                        "virtual_account_id": self._snapshot_account_value(account, "id"),
                        "provider": self._snapshot_account_value(account, "provider"),
                        "retry_count": self._snapshot_account_value(account, "retry_count", 0),
                        "status": self._snapshot_account_value(account, "status"),
                        "error": str(exc),
                    },
                )
                continue

    def _snapshot_account_value(self, account: Any, attribute_name: str, default: Any = None) -> Any:
        try:
            state = getattr(account, "_sa_instance_state", None)
            if state is not None:
                state_dict = getattr(state, "dict", None) or {}
                if attribute_name in state_dict:
                    return state_dict[attribute_name]
            if hasattr(account, "__dict__") and attribute_name in account.__dict__:
                return account.__dict__[attribute_name]
            return default
        except Exception:
            return default

    async def run_worker(self) -> None:
        while not self._stopped:
            try:
                await self.run_once()
            except Exception as exc:
                self.logger.exception("virtual_account_retry_worker_iteration_failed", extra={"error": str(exc)})
            if self._stopped:
                break
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._stopped = True

    def _build_service(self, *, redis_client: Any | None = None) -> VirtualAccountService:
        flutterwave_client = FlutterwaveClient()
        flutterwave_va = FlutterwaveVirtualAccountService(client=flutterwave_client)
        provider_map = {"flutterwave": flutterwave_va}
        from app.repositories.user_repository import UserRepository
        from app.repositories.virtual_account_repository import VirtualAccountRepository
        from app.repositories.wallet_repository import WalletRepository

        repository = VirtualAccountRepository(session=self.session)
        wallet_repository = WalletRepository(session=self.session)
        user_repository = UserRepository(session=self.session)
        return VirtualAccountService(
            virtual_account_repository=repository,
            wallet_repository=wallet_repository,
            user_repository=user_repository,
            provider_services=provider_map,
            session=self.session,
            logger=self.logger,
            redis_client=redis_client,
            lock_timeout_seconds=settings.virtual_account_retry_job_lock_timeout_seconds,
            retry_interval_seconds=settings.virtual_account_retry_job_retry_interval_seconds,
            max_retries=self.max_retries,
        )


async def start_background_retry_worker(app) -> None:
    """Create a background task attached to the ASGI app state once per app lifecycle."""
    if getattr(app.state, "virtual_account_retry_task", None) is not None and not getattr(app.state.virtual_account_retry_task, "done", False):
        return

    async def _worker() -> None:
        # Run repeated iterations where each iteration obtains its own DB session
        # to avoid holding a long-lived AsyncSession for the lifetime of the worker.
        try:
            while True:
                try:
                    async for session in get_db():
                        job = VirtualAccountRetryJob(
                            session=session,
                            poll_interval=settings.virtual_account_retry_job_interval_seconds,
                            max_per_batch=settings.virtual_account_retry_job_batch_size,
                            max_retries=settings.virtual_account_max_retries,
                        )
                        app.state.virtual_account_retry_job = job
                        try:
                            # Execute a single iteration (batch) using a fresh session.
                            await job.run_once()
                        finally:
                            # Clear the job reference so shutdown logic can proceed.
                            app.state.virtual_account_retry_job = None
                        # end async for -> session context will close here
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logging.getLogger(__name__).exception("virtual_account_retry_worker_iteration_failed", extra={"error": str(exc)})
                # Pause between iterations
                await asyncio.sleep(settings.virtual_account_retry_job_interval_seconds)
        finally:
            try:
                app.state.virtual_account_retry_job = None
            except Exception:
                pass

    task = asyncio.get_running_loop().create_task(_worker())
    app.state.virtual_account_retry_task = task


async def stop_background_retry_worker(app) -> None:
    task = getattr(app.state, "virtual_account_retry_task", None)
    job = getattr(app.state, "virtual_account_retry_job", None)
    if job is not None:
        job.stop()
    if task is not None and not task.done():
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception:
            return
