from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from app.models.provider import Provider
from app.services.provider_service import ProviderService
from app.utils.exceptions import ProviderException, ValidationException


class TVPackageService:
    """Retrieve and normalize TV subscription bouquets through provider orchestration."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def get_tv_packages(
        self,
        *,
        provider_name: str | None = None,
        force_refresh: bool = False,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return available TV packages from cache or the configured provider."""
        if provider_name is not None and not provider_name.strip():
            raise ValidationException("Provider name is invalid.")

        cache_key = self._cache_key(provider_name=provider_name)
        if not force_refresh:
            cached_packages = await self._get_cached_packages(cache_key)
            if cached_packages is not None:
                self.logger.info(
                    "tv_package_cache_hit",
                    extra={"cache_key": cache_key, "provider_name": provider_name},
                )
                return cached_packages

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required to retrieve TV packages.")

        self.logger.info(
            "tv_package_retrieval_started",
            extra={"cache_key": cache_key, "provider_name": provider_name, "force_refresh": force_refresh},
        )

        try:
            response = await self.provider_service.execute_tv(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={"provider_name": provider_name},
            )
        except Exception as exc:
            self.logger.warning(
                "tv_package_provider_error",
                extra={"cache_key": cache_key, "provider_name": provider_name, "error": str(exc)},
            )
            raise ProviderException(detail=str(exc)) from exc

        packages = self._extract_packages(response)
        self._validate_packages_response(packages)
        normalized_packages = [self._normalize_package(package, provider_name=provider_name) for package in packages]

        await self.cache_packages(
            normalized_packages,
            provider_name=provider_name,
            ttl_seconds=cache_ttl_seconds,
        )

        self.logger.info(
            "tv_package_retrieval_completed",
            extra={"cache_key": cache_key, "package_count": len(normalized_packages)},
        )
        return normalized_packages

    async def refresh_tv_packages(
        self,
        *,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Refresh TV package cache by fetching the latest provider payload."""
        self.logger.info("tv_package_cache_refresh", extra={"provider_name": provider_name})
        return await self.get_tv_packages(
            provider_name=provider_name,
            force_refresh=True,
            provider_operation=provider_operation,
            cache_ttl_seconds=cache_ttl_seconds,
        )

    async def get_package_by_id(
        self,
        *,
        package_id: str,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a single TV package by its identifier."""
        self._validate_package_id(package_id)
        packages = await self.get_tv_packages(
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        for package in packages:
            if self._matches_package_id(package, package_id):
                return package
        raise ValidationException("TV package was not found.")

    async def search_packages(
        self,
        *,
        query: str,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Search TV packages by a text query across common package fields."""
        if not query or not isinstance(query, str) or not query.strip():
            raise ValidationException("Search query is required.")

        packages = await self.get_tv_packages(
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        needle = query.strip().lower()
        return [
            package
            for package in packages
            if any(needle in str(package.get(field, "")).lower() for field in ("name", "provider_name", "code", "id", "description", "service_type"))
        ]

    async def filter_packages_by_provider(
        self,
        *,
        provider_name: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter TV packages by the advertising provider name."""
        if not provider_name or not isinstance(provider_name, str) or not provider_name.strip():
            raise ValidationException("Provider name is required.")

        packages = await self.get_tv_packages(
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        return [pkg for pkg in packages if str(pkg.get("provider_name", "")).lower() == provider_name.strip().lower()]

    async def cache_packages(
        self,
        packages: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
        *,
        provider_name: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        """Cache normalized TV packages in Redis when available."""
        if self.redis_client is None:
            return

        payload = [self._normalize_package(package, provider_name=provider_name) for package in list(packages or [])]
        cache_key = self._cache_key(provider_name=provider_name)

        try:
            await self.redis_client.set(cache_key, json.dumps(payload), ex=ttl_seconds or self.cache_ttl_seconds)
            self.logger.info("tv_package_cache_write", extra={"cache_key": cache_key, "package_count": len(payload)})
        except Exception as exc:
            self.logger.warning("tv_package_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def clear_package_cache(self, *, provider_name: str | None = None) -> None:
        """Remove cached TV package payloads for the supplied provider."""
        if self.redis_client is None:
            return

        cache_key = self._cache_key(provider_name=provider_name)
        try:
            await self.redis_client.delete(cache_key)
            self.logger.info("tv_package_cache_cleared", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("tv_package_cache_clear_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def _get_cached_packages(self, cache_key: str) -> list[dict[str, Any]] | None:
        if self.redis_client is None:
            return None

        try:
            raw_payload = await self.redis_client.get(cache_key)
            if not raw_payload:
                return None

            parsed = json.loads(raw_payload)
            if isinstance(parsed, list):
                return [self._normalize_package(package) for package in parsed]
            return None
        except Exception as exc:
            self.logger.warning("tv_package_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    def _cache_key(self, *, provider_name: str | None) -> str:
        parts = ["tv:packages"]
        if provider_name:
            parts.append(provider_name.strip().lower())
        return ":".join(parts)

    def _extract_packages(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(response, list):
            packages = response
        elif isinstance(response, dict):
            packages = (
                response.get("packages")
                or response.get("bouquets")
                or response.get("results")
                or response.get("data")
                or response.get("items")
                or []
            )
        else:
            packages = []

        if not isinstance(packages, list):
            raise ValidationException("Provider response does not contain TV package items.")
        return [package for package in packages if isinstance(package, dict)]

    def _normalize_package(self, package: dict[str, Any], *, provider_name: str | None = None) -> dict[str, Any]:
        if not isinstance(package, dict):
            raise ValidationException("TV package payload is invalid.")

        normalized: dict[str, Any] = {
            "id": package.get("id") or package.get("package_id") or package.get("code") or package.get("package_code"),
            "code": package.get("code") or package.get("package_code") or package.get("id"),
            "name": package.get("name") or package.get("package_name") or package.get("bundle_name") or package.get("description"),
            "provider_name": package.get("provider_name") or provider_name,
            "price": package.get("price") or package.get("amount") or package.get("cost"),
            "currency": package.get("currency") or package.get("currency_code") or "NGN",
            "duration": package.get("duration") or package.get("validity") or package.get("subscription_period"),
            "service_type": package.get("service_type") or package.get("bouquet") or package.get("package_type"),
            "description": package.get("description"),
            "status": package.get("status") or ("active" if package.get("is_active", True) else "inactive"),
        }

        return normalized

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required.")

    def _validate_packages_response(self, packages: list[dict[str, Any]]) -> None:
        if not packages:
            raise ValidationException("No TV packages were returned by the provider.")

    def _validate_package_id(self, package_id: str) -> None:
        if not package_id or not isinstance(package_id, str) or not package_id.strip():
            raise ValidationException("TV package ID is required.")

    def _matches_package_id(self, package: dict[str, Any], package_id: str) -> bool:
        candidate_id = str(package.get("id") or package.get("code") or "").strip().lower()
        return candidate_id == package_id.strip().lower()

    def _normalize_provider_response(self, provider_response: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(provider_response, list):
            return {"packages": provider_response, "provider_name": provider.name}
        if isinstance(provider_response, dict):
            normalized = dict(provider_response)
            if "provider_name" not in normalized or not normalized.get("provider_name"):
                normalized["provider_name"] = provider.name
            return normalized
        raise ValidationException("Provider response is invalid.")


__all__ = ["TVPackageService"]
