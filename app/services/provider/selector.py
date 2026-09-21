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


class ProviderSelector:
    """Select the best configured provider dynamically for a service category."""

    def __init__(
        self,
        *,
        provider_repository: ProviderRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_repository = provider_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def select_provider(
        self,
        *,
        category: str,
        service_type: str | None = None,
        environment: str | None = None,
        use_cache: bool = True,
    ) -> Provider:
        """Select the highest-ranked provider for the requested category."""
        self._ensure_category(category)
        cache_key = self._cache_key(category=category, service_type=service_type, environment=environment)

        if use_cache:
            cached = await self._get_cached_selection(cache_key)
            if cached is not None:
                return cached

        providers = await self.provider_repository.get_active_providers(category=category, service_type=service_type)
        if not providers:
            raise ValidationException("No active providers are available for the requested category.")

        candidates = [provider for provider in providers if self._is_eligible(provider, environment=environment)]
        if not candidates:
            raise ValidationException("No eligible providers are available for the requested category.")

        ranked = sorted(candidates, key=self._ranking_key, reverse=True)
        selected = ranked[0]

        if use_cache:
            await self._cache_selection(cache_key, selected)

        return selected

    async def invalidate_provider_cache(self, *, category: str | None = None) -> None:
        """Invalidate cached provider rankings when provider state changes."""
        if self.redis_client is None:
            return
        # Use non-blocking SCAN to iterate matching keys instead of KEYS (O(N) on Redis keyspace).
        base = self._cache_key(category=category, service_type=None, environment=None)
        pattern = f"{base}*"
        try:
            async for key in self.redis_client.scan_iter(match=pattern):
                try:
                    await self.redis_client.delete(key)
                except Exception:
                    # Best-effort deletion; continue removing other keys
                    self.logger.debug("provider_cache_delete_failed", extra={"key": str(key)})
        except Exception as exc:
            self.logger.warning("provider_cache_invalidation_failed", extra={"error": str(exc)})

    async def update_provider_priority(self, *, provider_id: UUID, priority: int) -> Provider:
        """Persist provider priority changes and invalidate selection caches."""
        provider = await self.provider_repository.get_provider_by_id(provider_id)
        if provider is None:
            raise ValidationException("Provider not found.")

        updated = await self.provider_repository.update_provider(provider, priority=int(priority))
        await self.invalidate_provider_cache(category=updated.category)
        return updated

    async def _get_cached_selection(self, cache_key: str) -> Provider | None:
        if self.redis_client is None:
            return None
        try:
            raw = await self.redis_client.get(cache_key)
            if not raw:
                return None
            payload = json.loads(raw)
            provider_id = payload.get("provider_id")
            if not provider_id:
                return None
            return await self.provider_repository.get_provider_by_id(UUID(provider_id))
        except Exception as exc:
            self.logger.warning("provider_cache_read_failed", extra={"error": str(exc)})
            return None

    async def _cache_selection(self, cache_key: str, provider: Provider) -> None:
        if self.redis_client is None:
            return
        try:
            payload = {"provider_id": str(provider.id), "cached_at": datetime.now(timezone.utc).isoformat()}
            await self.redis_client.set(cache_key, json.dumps(payload), ex=self.cache_ttl_seconds)
        except Exception as exc:
            self.logger.warning("provider_cache_write_failed", extra={"error": str(exc)})

    def _is_eligible(self, provider: Provider, *, environment: str | None) -> bool:
        if not provider.is_active:
            return False
        if provider.status.lower() not in {"active", "healthy", "ready"}:
            return False
        if environment and provider.environment.lower() != environment.lower():
            return False
        if self._is_in_maintenance(provider):
            return False
        if self._is_circuit_broken(provider):
            return False
        return True

    def _is_in_maintenance(self, provider: Provider) -> bool:
        metadata = self._parse_metadata(provider.metadata_payload)
        return bool(metadata.get("maintenance_mode"))

    def _is_circuit_broken(self, provider: Provider) -> bool:
        metadata = self._parse_metadata(provider.metadata_payload)
        failure_count = int(metadata.get("failure_count", 0))
        threshold = int(metadata.get("circuit_breaker_threshold", 5))
        return failure_count >= threshold

    def _ranking_key(self, provider: Provider) -> tuple[float, int, int, float, int]:
        metadata = self._parse_metadata(provider.metadata_payload)
        health_score = float(metadata.get("health_score", 0))
        failure_count = int(metadata.get("failure_count", 0))
        success_rate = float(metadata.get("success_rate", 0))
        priority = int(provider.priority or 0)
        category_weight = self._category_weight(provider.category)
        return (
            health_score,
            priority,
            category_weight,
            success_rate,
            -failure_count,
        )

    def _category_weight(self, category: str) -> int:
        mapping = {
            "Payments": 5,
            "Airtime": 4,
            "Data": 4,
            "Electricity": 4,
            "TV": 4,
            "Education": 3,
            "Gift Cards": 3,
            "SMS": 3,
            "Email": 2,
            "Cloud": 2,
        }
        return mapping.get(category, 1)

    def _cache_key(self, *, category: str, service_type: str | None, environment: str | None) -> str:
        parts = ["provider:selection", category.lower()]
        if service_type:
            parts.append(service_type.lower())
        if environment:
            parts.append(environment.lower())
        return ":".join(parts)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
        return {}

    def _ensure_category(self, category: str) -> None:
        if not category or not isinstance(category, str):
            raise ValidationException("Provider category is required.")
