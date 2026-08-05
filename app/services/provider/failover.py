from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

from app.models.provider import Provider
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.utils.exceptions import ProviderException, ValidationException

T = TypeVar("T")


class ProviderFailoverService:
    """Execute provider operations with automatic failover and resilience controls."""

    def __init__(
        self,
        *,
        selector: ProviderSelector,
        health_service: ProviderHealthService,
        logger: logging.Logger | None = None,
        max_retries: int = 3,
        provider_timeout_seconds: float = 10.0,
        backoff_base_seconds: float = 1.0,
        max_backoff_seconds: float = 8.0,
        jitter_factor: float = 0.2,
    ) -> None:
        self.selector = selector
        self.health_service = health_service
        self.logger = logger or logging.getLogger(__name__)
        self.max_retries = max_retries
        self.provider_timeout_seconds = provider_timeout_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter_factor = jitter_factor

    async def execute_with_failover(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        category: str,
        service_type: str | None = None,
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
    ) -> T:
        """Execute an operation across providers in priority order with failover support."""
        if not callable(operation):
            raise ValidationException("An operation callback is required.")

        providers = await self._get_candidate_providers(
            category=category,
            service_type=service_type,
            environment=environment,
            use_cache=use_cache,
        )
        if not providers:
            raise ValidationException("No eligible providers are available for the requested category.")

        retryable = retryable_errors or (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError)
        last_error: BaseException | None = None

        for attempt in range(self.max_retries + 1):
            provider = providers[attempt % len(providers)]
            if not await self._is_provider_eligible(provider, environment=environment):
                self.logger.warning(
                    "provider_failover_skipped",
                    extra={"provider_id": str(provider.id), "category": category, "attempt": attempt + 1},
                )
                continue

            try:
                started_at = time.perf_counter()
                result = await asyncio.wait_for(operation(provider), timeout=self.provider_timeout_seconds)
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
                await self.health_service.record_success(provider_id=provider.id, response_time_ms=elapsed_ms)
                self.logger.info(
                    "provider_failover_success",
                    extra={
                        "provider_id": str(provider.id),
                        "category": category,
                        "attempt": attempt + 1,
                        "response_time_ms": elapsed_ms,
                    },
                )
                return result
            except Exception as exc:  # pragma: no cover - exercised in runtime
                last_error = exc
                await self.health_service.record_failure(provider_id=provider.id, error=str(exc))
                self.logger.warning(
                    "provider_failover_failed",
                    extra={
                        "provider_id": str(provider.id),
                        "category": category,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    },
                )

                if not self._is_retryable(exc, retryable):
                    self.logger.warning(
                        "provider_failover_non_retryable",
                        extra={"provider_id": str(provider.id), "error": str(exc)},
                    )
                    break

                if attempt < self.max_retries:
                    delay = self._calculate_backoff(attempt)
                    self.logger.warning(
                        "provider_failover_retry_scheduled",
                        extra={
                            "provider_id": str(provider.id),
                            "category": category,
                            "attempt": attempt + 1,
                            "delay_seconds": round(delay, 3),
                        },
                    )
                    await asyncio.sleep(delay)
                    continue

        if last_error is None:
            raise ProviderException("All providers failed without a captured error.")

        if isinstance(last_error, ValidationException):
            raise last_error
        if isinstance(last_error, ProviderException):
            raise last_error
        raise ProviderException(detail=str(last_error)) from last_error

    async def _get_candidate_providers(
        self,
        *,
        category: str,
        service_type: str | None,
        environment: str | None,
        use_cache: bool,
    ) -> list[Provider]:
        providers = await self.selector.provider_repository.get_active_providers(
            category=category,
            service_type=service_type,
        )
        eligible = [provider for provider in providers if self.selector._is_eligible(provider, environment=environment)]
        ranked = sorted(eligible, key=self.selector._ranking_key, reverse=True)
        if not ranked:
            return []
        if not use_cache:
            return ranked
        return ranked

    async def _is_provider_eligible(self, provider: Provider, *, environment: str | None) -> bool:
        if not provider.is_active:
            return False
        if not await self.health_service.is_provider_healthy(provider_id=provider.id):
            return False
        return self.selector._is_eligible(provider, environment=environment)

    def _calculate_backoff(self, attempt: int) -> float:
        base = min(self.backoff_base_seconds * (2**attempt), self.max_backoff_seconds)
        jitter = random.uniform(0.0, self.jitter_factor * base)
        return round(base + jitter, 3)

    def _is_retryable(self, error: BaseException, retryable_errors: tuple[type[BaseException], ...]) -> bool:
        if isinstance(error, ValidationException):
            return False
        return isinstance(error, retryable_errors)
