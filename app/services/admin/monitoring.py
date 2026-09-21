from __future__ import annotations

import json
import logging
from typing import Any

from app.repositories.provider_repository import ProviderRepository
from app.services.provider.health import ProviderHealthService


class MonitoringService:
    """Service for platform health and service monitoring."""

    def __init__(
        self,
        *,
        provider_repository: ProviderRepository,
        provider_health_service: ProviderHealthService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_repository = provider_repository
        self.provider_health_service = provider_health_service
        self.logger = logger or logging.getLogger(__name__)

    async def get_service_monitoring(self, **payload: Any) -> dict[str, Any]:
        providers, total_providers = await self.provider_repository.get_all_providers(page=1, page_size=1000)
        provider_data: list[dict[str, Any]] = []
        healthy_count = 0
        unhealthy_count = 0

        for provider in providers:
            metadata = self._parse_metadata(provider.metadata_payload)
            health_score = None
            if self.provider_health_service is not None:
                provider_health = await self.provider_health_service.calculate_health_score(provider_id=provider.id)
                health_score = provider_health.get("health_score")

            provider_level = "healthy" if provider.health_status and provider.health_status.lower() == "healthy" else "unhealthy"
            if provider_level == "healthy":
                healthy_count += 1
            else:
                unhealthy_count += 1

            provider_data.append(
                {
                    "id": str(provider.id),
                    "name": provider.name,
                    "category": provider.category,
                    "status": provider.status,
                    "is_active": provider.is_active,
                    "health_status": provider.health_status,
                    "health_score": health_score,
                    "last_checked_at": provider.last_checked_at.isoformat() if provider.last_checked_at else None,
                    "metadata": metadata,
                }
            )

        return {
            "success": True,
            "data": {
                "provider_count": total_providers,
                "healthy_providers": healthy_count,
                "unhealthy_providers": unhealthy_count,
                "providers": provider_data,
            },
            "meta": {"source": "admin.monitoring"},
        }

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"value": payload}
