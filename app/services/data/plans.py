from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from app.models.provider import Provider
from app.integrations.airtime.manager import ProviderManager
from app.services.provider_service import ProviderService
from app.utils.exceptions import ProviderException, ValidationException


class DataPlanService:
    """Retrieve and cache data plans through provider orchestration."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        provider_manager: ProviderManager | None = None,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.provider_manager = provider_manager or ProviderManager()
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def get_data_plans(
        self,
        *,
        network: str | None = None,
        provider_name: str | None = None,
        force_refresh: bool = False,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return available data plans from cache or provider service."""
        if network is not None:
            self._validate_network(network)
        if provider_name is not None and not provider_name.strip():
            raise ValidationException("Provider name is invalid.")

        cache_key = self._cache_key(network=network, provider_name=provider_name)
        if not force_refresh:
            cached_plans = await self._get_cached_plans(cache_key)
            if cached_plans is not None:
                self.logger.info("data_plan_cache_hit", extra={"cache_key": cache_key, "network": network, "provider_name": provider_name})
                return cached_plans

        self.logger.info(
            "data_plan_retrieval_started",
            extra={"cache_key": cache_key, "network": network, "provider_name": provider_name, "force_refresh": force_refresh},
        )
        try:
            response = self._normalize_provider_manager_response(
                await self.provider_manager.execute(
                    "fetch_data_plans",
                    network=network,
                )
            )
        except Exception as exc:
            self.logger.warning(
                "data_plan_provider_error",
                extra={"cache_key": cache_key, "network": network, "provider_name": provider_name, "error": str(exc)},
            )
            raise ProviderException(detail=str(exc)) from exc

        plans = self._extract_plans(response)
        self._validate_plans_response(plans)
        normalized_plans = [self._normalize_plan(plan, network=network, provider_name=provider_name) for plan in plans]
        await self.cache_plans(
            normalized_plans,
            network=network,
            provider_name=provider_name,
            ttl_seconds=cache_ttl_seconds,
        )
        self.logger.info("data_plan_retrieval_completed", extra={"cache_key": cache_key, "plan_count": len(normalized_plans)})
        return normalized_plans

    async def refresh_data_plans(
        self,
        *,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Refresh cached data plans by fetching the latest provider payload."""
        self.logger.info("data_plan_cache_refresh", extra={"network": network, "provider_name": provider_name})
        return await self.get_data_plans(
            network=network,
            provider_name=provider_name,
            force_refresh=True,
            provider_operation=provider_operation,
            cache_ttl_seconds=cache_ttl_seconds,
        )

    async def get_plan_by_id(
        self,
        *,
        plan_id: str,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a single plan by its identifier."""
        self._validate_plan_id(plan_id)
        plans = await self.get_data_plans(
            network=network,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        for plan in plans:
            if self._matches_plan_id(plan, plan_id):
                return plan
        raise ValidationException("Data plan was not found.")

    async def search_plans(
        self,
        *,
        query: str,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Search plans by a text query across common plan fields."""
        if not query or not isinstance(query, str) or not query.strip():
            raise ValidationException("Search query is required.")

        plans = await self.get_data_plans(
            network=network,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        needle = query.strip().lower()
        return [
            plan
            for plan in plans
            if any(needle in str(plan.get(field, "")).lower() for field in ("name", "provider_name", "network", "id", "code", "bundle_code", "description"))
        ]

    async def filter_plans_by_network(
        self,
        *,
        network: str,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter plans by the mobile network they support."""
        self._validate_network(network)
        plans = await self.get_data_plans(
            network=network,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        return [plan for plan in plans if str(plan.get("network", "")).lower() == network.lower()]

    async def filter_plans_by_provider(
        self,
        *,
        provider_name: str,
        network: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter plans by the provider that advertises them."""
        if not provider_name or not provider_name.strip():
            raise ValidationException("Provider name is required.")

        plans = await self.get_data_plans(
            network=network,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        return [plan for plan in plans if str(plan.get("provider_name", "")).lower() == provider_name.strip().lower()]

    async def cache_plans(
        self,
        plans: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
        *,
        network: str | None = None,
        provider_name: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Cache normalized data plans in Redis when available."""
        if self.redis_client is None:
            return

        payload = [self._normalize_plan(plan, network=network, provider_name=provider_name) for plan in list(plans or [])]
        cache_key = self._cache_key(network=network, provider_name=provider_name)
        try:
            await self.redis_client.set(cache_key, json.dumps(payload), ex=ttl_seconds or self.cache_ttl_seconds)
            self.logger.info("data_plan_cache_write", extra={"cache_key": cache_key, "plan_count": len(payload)})
        except Exception as exc:
            self.logger.warning("data_plan_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def clear_plan_cache(self, *, network: str | None = None, provider_name: str | None = None) -> None:
        """Remove cached data-plan payloads for the supplied filters."""
        if self.redis_client is None:
            return

        cache_key = self._cache_key(network=network, provider_name=provider_name)
        try:
            await self.redis_client.delete(cache_key)
            self.logger.info("data_plan_cache_cleared", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("data_plan_cache_clear_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def _get_cached_plans(self, cache_key: str) -> list[dict[str, Any]] | None:
        if self.redis_client is None:
            return None
        try:
            raw_payload = await self.redis_client.get(cache_key)
            if not raw_payload:
                return None
            parsed = json.loads(raw_payload)
            if isinstance(parsed, list):
                return [self._normalize_plan(plan) for plan in parsed]
            return None
        except Exception as exc:
            self.logger.warning("data_plan_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    def _cache_key(self, *, network: str | None, provider_name: str | None) -> str:
        parts = ["data:plans"]
        if network:
            parts.append(network.lower())
        if provider_name:
            parts.append(provider_name.lower())
        return ":".join(parts)

    def _normalize_plan(self, plan: dict[str, Any], *, network: str | None = None, provider_name: str | None = None) -> dict[str, Any]:
        if not isinstance(plan, dict):
            raise ValidationException("Provider plan payload is invalid.")

        normalized: dict[str, Any] = {
            "id": plan.get("id") or plan.get("plan_id") or plan.get("code") or plan.get("reference") or plan.get("bundle_code"),
            "code": plan.get("code") or plan.get("bundle_code") or plan.get("plan_id") or plan.get("id"),
            "name": plan.get("name") or plan.get("plan_name") or plan.get("bundle_name") or plan.get("description"),
            "network": plan.get("network") or network,
            "provider_name": plan.get("provider_name") or provider_name,
            "price": plan.get("price") or plan.get("amount") or plan.get("cost"),
            "description": plan.get("description") or plan.get("details"),
            "validity_period": plan.get("validity_period") or plan.get("validity"),
            "currency": plan.get("currency") or "NGN",
        }
        if plan.get("metadata"):
            normalized["metadata"] = plan.get("metadata")
        if plan.get("features"):
            normalized["features"] = plan.get("features")
        return normalized

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "provider": provider.name,
            "plans": self._extract_plans(payload),
            "metadata": payload.get("metadata"),
        }

    def _normalize_provider_manager_response(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ProviderException(detail="Provider response is invalid.")

        provider_name = result.get("provider")
        payload = result.get("data") if isinstance(result.get("data"), dict) else result

        response_data = payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            response_data = payload["data"]

        metadata = None
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
        if metadata is None and isinstance(response_data, dict):
            metadata = response_data.get("metadata")

        return {
            "provider": provider_name or (response_data.get("provider") if isinstance(response_data, dict) else None),
            "plans": self._extract_plans(response_data),
            "metadata": metadata,
            "raw": response_data,
        }

    def _extract_plans(self, response: Any) -> list[dict[str, Any]]:
        if isinstance(response, list):
            return [plan for plan in response if isinstance(plan, dict)]
        if isinstance(response, dict):
            if isinstance(response.get("data"), dict):
                nested = response["data"]
                if isinstance(nested.get("plans"), list):
                    return [plan for plan in nested["plans"] if isinstance(plan, dict)]
                if isinstance(nested.get("items"), list):
                    return [plan for plan in nested["items"] if isinstance(plan, dict)]
                if isinstance(nested.get("result"), list):
                    return [plan for plan in nested["result"] if isinstance(plan, dict)]
                if isinstance(nested.get("plan"), dict):
                    return [nested["plan"]]
            for key in ("plans", "data", "result", "items"):
                value = response.get(key)
                if isinstance(value, list):
                    return [plan for plan in value if isinstance(plan, dict)]
            if isinstance(response.get("plan"), dict):
                return [response["plan"]]
        return []

    def _validate_plans_response(self, plans: list[dict[str, Any]]) -> None:
        if plans is None:
            raise ValidationException("Provider returned no data plans.")
        for plan in plans:
            if not isinstance(plan, dict):
                raise ValidationException("Provider returned an invalid data plan payload.")
            if not plan.get("id") and not plan.get("plan_id") and not plan.get("code") and not plan.get("bundle_code"):
                raise ValidationException("Provider returned a plan without an identifier.")

    def _validate_network(self, network: str) -> None:
        if not network or not isinstance(network, str) or not network.strip():
            raise ValidationException("Network is required.")

    def _validate_plan_id(self, plan_id: str) -> None:
        if not plan_id or not isinstance(plan_id, str) or not plan_id.strip():
            raise ValidationException("Plan ID is required.")

    def _matches_plan_id(self, plan: dict[str, Any], plan_id: str) -> bool:
        plan_key_candidates = ("id", "plan_id", "code", "bundle_code", "reference")
        identifier = plan_id.strip().lower()
        for field in plan_key_candidates:
            value = plan.get(field)
            if isinstance(value, str) and value.strip().lower() == identifier:
                return True
        return False


__all__ = ["DataPlanService"]
