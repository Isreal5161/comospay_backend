from __future__ import annotations

"""Provider route registration for the CosmozPay backend."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.provider_controller import ProviderController
from app.repositories.provider_repository import ProviderRepository
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService

router = APIRouter(prefix="/providers", tags=["Providers"])


async def get_provider_service(session: AsyncSession = Depends(get_db)) -> ProviderService:
    """Compose the provider service graph per request using the active database session."""
    provider_repository = ProviderRepository(session=session)
    selector = ProviderSelector(provider_repository=provider_repository)
    health_service = ProviderHealthService(provider_repository=provider_repository)
    failover_service = ProviderFailoverService(selector=selector, health_service=health_service)

    return ProviderService(
        selector=selector,
        health_service=health_service,
        failover_service=failover_service,
        provider_repository=provider_repository,
    )


async def get_provider_controller(
    provider_service: ProviderService = Depends(get_provider_service),
) -> ProviderController:
    """Instantiate the provider controller with a request-scoped provider service."""
    return ProviderController(provider_service)


@router.get("/health-status/{provider_id}", status_code=status.HTTP_200_OK)
async def get_health_status(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_health_status(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/availability/{provider_id}", status_code=status.HTTP_200_OK)
async def get_availability(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_availability(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/config/{provider_id}", status_code=status.HTTP_200_OK)
async def get_configuration(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_configuration(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/failover/{provider_id}", status_code=status.HTTP_200_OK)
async def get_failover_status(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_failover_status(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/circuit-breaker/{provider_id}", status_code=status.HTTP_200_OK)
async def get_circuit_breaker_status(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_circuit_breaker_status(provider_id, category=category, service_type=service_type, environment=environment)


@router.post("/health-check/{provider_id}", status_code=status.HTTP_200_OK)
async def run_health_check(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.run_health_check(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/metrics/{provider_id}", status_code=status.HTTP_200_OK)
async def get_metrics(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_metrics(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/retry-status/{provider_id}", status_code=status.HTTP_200_OK)
async def get_retry_status(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_retry_status(provider_id, category=category, service_type=service_type, environment=environment)


@router.get("/performance-summary/{provider_id}", status_code=status.HTTP_200_OK)
async def get_performance_summary(
    provider_id: UUID,
    category: str | None = None,
    service_type: str | None = None,
    environment: str | None = None,
    controller: ProviderController = Depends(get_provider_controller),
) -> dict[str, Any]:
    return await controller.get_performance_summary(provider_id, category=category, service_type=service_type, environment=environment)
