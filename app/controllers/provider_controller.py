from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.provider_service import ProviderService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class ProviderRequest(BaseModel):
    """Request schema for provider lookup and management operations."""

    provider_id: UUID = Field(..., description="Identifier of the provider to inspect.")
    category: str | None = Field(default=None, description="Optional provider category filter.")
    service_type: str | None = Field(default=None, description="Optional service type filter.")
    environment: str | None = Field(default=None, description="Optional environment filter.")


class ProviderAdminRequest(ProviderRequest):
    """Request schema for administrator-only provider management operations."""

    require_admin: bool = Field(default=False, description="Whether the caller must be an administrator.")


class ProviderController:
    """Thin FastAPI controller for provider management and health endpoints."""

    def __init__(self, provider_service: ProviderService, logger: logging.Logger | None = None) -> None:
        self.provider_service = provider_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/providers", tags=["Providers"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.get("/health-status/{provider_id}", status_code=status.HTTP_200_OK)(self.get_health_status)
        self.router.get("/availability/{provider_id}", status_code=status.HTTP_200_OK)(self.get_availability)
        self.router.get("/config/{provider_id}", status_code=status.HTTP_200_OK)(self.get_configuration)
        self.router.get("/failover/{provider_id}", status_code=status.HTTP_200_OK)(self.get_failover_status)
        self.router.get("/circuit-breaker/{provider_id}", status_code=status.HTTP_200_OK)(self.get_circuit_breaker_status)
        self.router.post("/health-check/{provider_id}", status_code=status.HTTP_200_OK)(self.run_health_check)
        self.router.get("/metrics/{provider_id}", status_code=status.HTTP_200_OK)(self.get_metrics)
        self.router.get("/retry-status/{provider_id}", status_code=status.HTTP_200_OK)(self.get_retry_status)
        self.router.get("/performance-summary/{provider_id}", status_code=status.HTTP_200_OK)(self.get_performance_summary)

    async def get_health_status(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider health status requests."""
        return await self._execute(
            action="get_health_status",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id)},
            },
            success_message="Provider health status retrieved successfully.",
        )

    async def get_availability(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider availability requests."""
        return await self._execute(
            action="get_availability",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "availability": True},
            },
            success_message="Provider availability retrieved successfully.",
        )

    async def get_configuration(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider configuration retrieval requests."""
        return await self._execute(
            action="get_configuration",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "configuration": True},
            },
            success_message="Provider configuration retrieved successfully.",
        )

    async def get_failover_status(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider failover status requests."""
        return await self._execute(
            action="get_failover_status",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "failover_status": "configured"},
            },
            success_message="Provider failover status retrieved successfully.",
        )

    async def get_circuit_breaker_status(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider circuit breaker status requests."""
        return await self._execute(
            action="get_circuit_breaker_status",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "circuit_breaker_status": "configured"},
            },
            success_message="Provider circuit breaker status retrieved successfully.",
        )

    async def run_health_check(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider health check requests."""
        return await self._execute(
            action="run_health_check",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "health_check": True},
            },
            success_message="Provider health check completed successfully.",
        )

    async def get_metrics(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider metrics requests."""
        return await self._execute(
            action="get_metrics",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "metrics": True},
            },
            success_message="Provider metrics retrieved successfully.",
        )

    async def get_retry_status(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider retry status requests."""
        return await self._execute(
            action="get_retry_status",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "retry_status": "available"},
            },
            success_message="Provider retry status retrieved successfully.",
        )

    async def get_performance_summary(self, provider_id: UUID, category: str | None = None, service_type: str | None = None, environment: str | None = None) -> dict[str, Any]:
        """Handle provider performance summary requests."""
        return await self._execute(
            action="get_performance_summary",
            handler=self.provider_service.execute_provider,
            payload={
                "category": category or "Provider",
                "operation": self._noop_operation,
                "service_type": service_type,
                "environment": environment,
                "payload": {"provider_id": str(provider_id), "performance_summary": True},
            },
            success_message="Provider performance summary retrieved successfully.",
        )

    async def _execute(
        self,
        action: str,
        handler: Callable[..., Awaitable[Any]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            result = await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

        log_api_event(self.logger, "provider_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    async def _invoke_provider_service(
        self,
        action: str,
        handler: Callable[..., Awaitable[Any]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            return await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

    async def _noop_operation(self, provider: Any) -> dict[str, Any]:
        return {"provider_id": str(getattr(provider, "id", "unknown")), "status": "ok"}

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "provider_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )
