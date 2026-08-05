from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.models.provider import Provider
from app.repositories.provider_repository import ProviderRepository
from app.utils.exceptions import ValidationException


class ProviderHealthService:
    """Track provider health metrics and update provider availability state."""

    def __init__(
        self,
        *,
        provider_repository: ProviderRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_repository = provider_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)

    async def record_success(self, *, provider_id: UUID, response_time_ms: float | int | None = None) -> dict[str, Any]:
        """Record a successful provider request and update health metrics."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        metrics["success_count"] = int(metrics.get("success_count", 0)) + 1
        metrics["consecutive_failures"] = 0
        metrics["last_successful_request"] = self._utc_now_iso()
        if response_time_ms is not None:
            metrics["average_response_time_ms"] = self._average_value(metrics.get("average_response_time_ms"), float(response_time_ms))
        await self._persist_metrics(provider, metrics)
        await self.mark_provider_available(provider_id=provider.id)
        return self._build_metrics_payload(provider.id, metrics)

    async def record_failure(self, *, provider_id: UUID, error: str | None = None, response_time_ms: float | int | None = None) -> dict[str, Any]:
        """Record a failed provider request and update health metrics."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        metrics["failure_count"] = int(metrics.get("failure_count", 0)) + 1
        metrics["consecutive_failures"] = int(metrics.get("consecutive_failures", 0)) + 1
        metrics["last_failed_request"] = self._utc_now_iso()
        if response_time_ms is not None:
            metrics["average_response_time_ms"] = self._average_value(metrics.get("average_response_time_ms"), float(response_time_ms))
        if error:
            metrics.setdefault("errors", [])
            errors = list(metrics["errors"])
            errors.append(error)
            metrics["errors"] = errors[-10:]
        await self._persist_metrics(provider, metrics)
        await self.increment_failure_count(provider_id=provider.id)
        return self._build_metrics_payload(provider.id, metrics)

    async def calculate_health_score(self, *, provider_id: UUID) -> dict[str, Any]:
        """Calculate a provider health score from current metrics."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        success_count = int(metrics.get("success_count", 0))
        failure_count = int(metrics.get("failure_count", 0))
        total_requests = success_count + failure_count
        success_rate = (success_count / total_requests) if total_requests else 1.0
        failure_rate = 1.0 - success_rate
        consequence_penalty = min(int(metrics.get("consecutive_failures", 0)) * 0.1, 1.0)
        score = max(0.0, min(100.0, (success_rate * 100.0) - (failure_rate * 40.0) - (consequence_penalty * 20.0)))
        metrics["health_score"] = round(score, 2)
        metrics["success_rate"] = round(success_rate, 4)
        metrics["failure_rate"] = round(failure_rate, 4)
        await self._persist_metrics(provider, metrics)
        return {"provider_id": str(provider.id), "health_score": round(score, 2), "success_rate": round(success_rate, 4), "failure_rate": round(failure_rate, 4)}

    async def update_provider_status(self, *, provider_id: UUID) -> dict[str, Any]:
        """Update provider availability and status based on the latest metrics."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        score = float(metrics.get("health_score", self._fallback_score(provider)))
        if score < 50 or int(metrics.get("consecutive_failures", 0)) >= 3:
            await self.mark_provider_unavailable(provider_id=provider.id, reason="health_threshold")
        else:
            await self.mark_provider_available(provider_id=provider.id)
        return {"provider_id": str(provider.id), "status": provider.status, "health_score": score}

    async def is_provider_healthy(self, *, provider_id: UUID) -> bool:
        """Return whether the provider is considered healthy for routing."""
        provider = await self._get_provider(provider_id)
        return provider.is_active and provider.status.lower() in {"active", "healthy", "ready"}

    async def increment_failure_count(self, *, provider_id: UUID) -> dict[str, Any]:
        """Increase the failure count tracked in provider metadata."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        metrics["failure_count"] = int(metrics.get("failure_count", 0)) + 1
        await self._persist_metrics(provider, metrics)
        return {"provider_id": str(provider.id), "failure_count": int(metrics.get("failure_count", 0))}

    async def reset_failure_count(self, *, provider_id: UUID) -> dict[str, Any]:
        """Reset the failure counter after recovery."""
        provider = await self._get_provider(provider_id)
        metrics = self._parse_metrics(provider.metadata_payload)
        metrics["failure_count"] = 0
        metrics["consecutive_failures"] = 0
        await self._persist_metrics(provider, metrics)
        return {"provider_id": str(provider.id), "failure_count": 0}

    async def mark_provider_available(self, *, provider_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Mark a provider as available and healthy for routing."""
        provider = await self._get_provider(provider_id)
        provider.is_active = True
        provider.status = "active"
        provider.health_status = "healthy"
        provider.last_checked_at = datetime.now(timezone.utc)
        metadata = self._parse_metrics(provider.metadata_payload)
        metadata["last_available_at"] = self._utc_now_iso()
        if reason:
            metadata["availability_reason"] = reason
        provider.metadata_payload = self._serialize_metrics(metadata)
        await self.provider_repository.update_provider(provider, is_active=True, status="active", health_status="healthy", last_checked_at=provider.last_checked_at, metadata_payload=provider.metadata_payload)
        await self._cache_metrics(provider)
        return {"provider_id": str(provider.id), "status": provider.status, "healthy": True}

    async def mark_provider_unavailable(self, *, provider_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Mark a provider as unavailable and unhealthy for routing."""
        provider = await self._get_provider(provider_id)
        provider.is_active = False
        provider.status = "degraded"
        provider.health_status = "unhealthy"
        provider.last_checked_at = datetime.now(timezone.utc)
        metadata = self._parse_metrics(provider.metadata_payload)
        metadata["last_unavailable_at"] = self._utc_now_iso()
        if reason:
            metadata["availability_reason"] = reason
        provider.metadata_payload = self._serialize_metrics(metadata)
        await self.provider_repository.update_provider(provider, is_active=False, status="degraded", health_status="unhealthy", last_checked_at=provider.last_checked_at, metadata_payload=provider.metadata_payload)
        await self._cache_metrics(provider)
        return {"provider_id": str(provider.id), "status": provider.status, "healthy": False}

    async def _persist_metrics(self, provider: Provider, metrics: dict[str, Any]) -> None:
        provider.metadata_payload = self._serialize_metrics(metrics)
        await self.provider_repository.update_provider(provider, metadata_payload=provider.metadata_payload)
        await self._cache_metrics(provider)

    async def _cache_metrics(self, provider: Provider) -> None:
        if self.redis_client is None:
            return
        try:
            payload = {"provider_id": str(provider.id), "metadata": self._parse_metrics(provider.metadata_payload)}
            await self.redis_client.set(f"provider:health:{provider.id}", json.dumps(payload), ex=300)
        except Exception as exc:
            self.logger.warning("provider_health_cache_write_failed", extra={"error": str(exc)})

    def _build_metrics_payload(self, provider_id: UUID, metrics: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider_id": str(provider_id),
            "success_count": int(metrics.get("success_count", 0)),
            "failure_count": int(metrics.get("failure_count", 0)),
            "consecutive_failures": int(metrics.get("consecutive_failures", 0)),
            "average_response_time_ms": metrics.get("average_response_time_ms"),
            "last_successful_request": metrics.get("last_successful_request"),
            "last_failed_request": metrics.get("last_failed_request"),
            "health_score": metrics.get("health_score"),
            "success_rate": metrics.get("success_rate"),
            "failure_rate": metrics.get("failure_rate"),
        }

    async def _get_provider(self, provider_id: UUID) -> Provider:
        provider = await self.provider_repository.get_provider_by_id(provider_id)
        if provider is None:
            raise ValidationException("Provider not found.")
        return provider

    def _parse_metrics(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
        return {}

    def _serialize_metrics(self, metrics: dict[str, Any]) -> str | None:
        if not metrics:
            return None
        return json.dumps(metrics, default=str)

    def _average_value(self, previous: Any, current: float) -> float:
        if previous is None:
            return current
        if isinstance(previous, (int, float)):
            return round((float(previous) + current) / 2.0, 2)
        return current

    def _fallback_score(self, provider: Provider) -> float:
        return 100.0 if provider.is_active and provider.status.lower() in {"active", "healthy", "ready"} else 0.0

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
