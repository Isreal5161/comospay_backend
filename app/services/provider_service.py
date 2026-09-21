from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, TypeVar

from app.models.provider import Provider
from app.repositories.provider_repository import ProviderRepository
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.utils.exceptions import ProviderException, ValidationException

T = TypeVar("T")


class ProviderService:
    """Public orchestration facade for provider execution and health management."""

    def __init__(
        self,
        *,
        selector: ProviderSelector,
        health_service: ProviderHealthService,
        failover_service: ProviderFailoverService,
        provider_repository: ProviderRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.selector = selector
        self.health_service = health_service
        self.failover_service = failover_service
        self.provider_repository = provider_repository
        self.logger = logger or logging.getLogger(__name__)

    async def execute_provider(
        self,
        *,
        category: str,
        operation: Callable[[Provider], Awaitable[T]],
        service_type: str | None = None,
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a provider operation through the shared failover and health flow."""
        if not category or not isinstance(category, str):
            raise ValidationException("Provider category is required.")
        if not callable(operation):
            raise ValidationException("An operation callback is required.")

        if validate is not None:
            validate(payload or {})

        selected_provider = await self.selector.select_provider(
            category=category,
            service_type=service_type,
            environment=environment,
            use_cache=use_cache,
        )
        provider_record = await self.provider_repository.get_provider_by_id(selected_provider.id)
        if provider_record is None:
            raise ProviderException("Selected provider could not be resolved.")

        started_at = self._now_ms()
        self.logger.info(
            "provider_execution_started",
            extra={
                "provider": provider_record.name,
                "category": category,
                "service_type": service_type,
                "environment": environment,
            },
        )

        try:
            executed_provider: Provider | None = None

            async def tracked_operation(provider: Provider) -> T:
                nonlocal executed_provider
                executed_provider = provider
                return await operation(provider)

            result = await self.failover_service.execute_with_failover(
                operation=tracked_operation,
                category=category,
                service_type=service_type,
                environment=environment,
                use_cache=use_cache,
                retryable_errors=retryable_errors,
            )
            resolved_provider = executed_provider or provider_record
            response = normalize(result, resolved_provider) if normalize is not None else self._default_normalize(resolved_provider, result, category=category, service_type=service_type)
            elapsed_ms = self._now_ms() - started_at
            self.logger.info(
                "provider_execution_completed",
                extra={
                    "provider": resolved_provider.name,
                    "category": category,
                    "service_type": service_type,
                    "status": "success",
                    "execution_time_ms": round(elapsed_ms, 2),
                },
            )
            return response
        except Exception as exc:
            elapsed_ms = self._now_ms() - started_at
            self.logger.warning(
                "provider_execution_failed",
                extra={
                    "provider": provider_record.name,
                    "category": category,
                    "service_type": service_type,
                    "status": "failed",
                    "execution_time_ms": round(elapsed_ms, 2),
                    "error": str(exc),
                },
            )
            if isinstance(exc, ValidationException):
                raise
            if isinstance(exc, ProviderException):
                raise
            raise ProviderException(detail=str(exc)) from exc

    async def execute_payment(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        service_type: str | None = None,
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a payment request through the shared provider orchestration flow."""
        return await self.execute_provider(
            category="Payments",
            operation=operation,
            service_type=service_type,
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_virtual_account(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a virtual-account flow through the payment provider stack."""
        return await self.execute_payment(
            operation=operation,
            service_type="virtual_account",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_transfer(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a transfer request through the payment provider stack."""
        return await self.execute_payment(
            operation=operation,
            service_type="transfer",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_airtime(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an airtime purchase through the VTU provider stack."""
        return await self.execute_provider(
            category="VTU",
            operation=operation,
            service_type="airtime",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_data(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a data purchase through the VTU provider stack."""
        return await self.execute_provider(
            category="VTU",
            operation=operation,
            service_type="data",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_electricity(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an electricity purchase through the VTU provider stack."""
        return await self.execute_provider(
            category="VTU",
            operation=operation,
            service_type="electricity",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_tv(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a TV subscription through the VTU provider stack."""
        return await self.execute_provider(
            category="VTU",
            operation=operation,
            service_type="tv",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_education(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an education-service request through the education provider stack."""
        return await self.execute_provider(
            category="Education",
            operation=operation,
            service_type="education",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_giftcard(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a gift-card request through the gift-card provider stack."""
        return await self.execute_provider(
            category="Gift Cards",
            operation=operation,
            service_type="giftcard",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_sms(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an SMS request through the messaging provider stack."""
        return await self.execute_provider(
            category="SMS",
            operation=operation,
            service_type="sms",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_email(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an email request through the email provider stack."""
        return await self.execute_provider(
            category="Email",
            operation=operation,
            service_type="email",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_cloud_upload(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a cloud upload through the cloud provider stack."""
        return await self.execute_provider(
            category="Cloud",
            operation=operation,
            service_type="upload",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    async def execute_cloud_delete(
        self,
        *,
        operation: Callable[[Provider], Awaitable[T]],
        environment: str | None = None,
        use_cache: bool = True,
        retryable_errors: tuple[type[BaseException], ...] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
        normalize: Callable[[Provider, T], dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a cloud delete through the cloud provider stack."""
        return await self.execute_provider(
            category="Cloud",
            operation=operation,
            service_type="delete",
            environment=environment,
            use_cache=use_cache,
            retryable_errors=retryable_errors,
            validate=validate,
            normalize=normalize,
            payload=payload,
        )

    def _default_normalize(self, provider: Provider, result: T, *, category: str, service_type: str | None) -> dict[str, Any]:
        """Convert provider-specific output into a normalized domain response."""
        if isinstance(result, dict):
            payload = {"data": result}
        else:
            payload = {"data": result}
        return {
            "status": "success",
            "provider": {
                "id": str(provider.id),
                "name": provider.name,
                "code": provider.code,
                "category": category,
                "service_type": service_type,
                "status": provider.status,
            },
            "result": payload,
        }

    def _now_ms(self) -> float:
        return self._monotonic_ms()

    def _monotonic_ms(self) -> float:
        import time

        return time.perf_counter() * 1000.0
