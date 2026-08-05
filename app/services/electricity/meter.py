from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.integrations.airtime.exceptions import NoProviderAvailableError
from app.integrations.airtime.manager import ProviderManager
from app.models.provider import Provider
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.services.electricity.validation import ElectricityValidationService
from app.services.provider_service import ProviderService
from app.utils.exceptions import ProviderException, ValidationException


class ElectricityMeterService:
    """Resolve and verify electricity meter details through the provider stack."""

    CACHE_KEY_TEMPLATE = "electricity:meter:verification:{provider}:{disco}:{meter_hash}"
    SETTINGS_PREFIX = "electricity_meter"
    PROVIDER_SETTING_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    DEFAULT_SETTING_TEMPLATE = "{prefix}:{key}"

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        provider_manager: ProviderManager | None = None,
        settings_repository: SystemSettingsRepository,
        validation_service: ElectricityValidationService | None = None,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.provider_manager = provider_manager or ProviderManager()
        self.settings_repository = settings_repository
        self.validation_service = validation_service
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def verify_meter(
        self,
        *,
        meter_number: str,
        disco: str,
        meter_type: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Verify a meter through the electricity provider and normalize the verified data."""
        normalized_meter_number = self._normalize_meter_number(meter_number)
        normalized_disco = self._normalize_disco(disco)
        normalized_meter_type = self._normalize_meter_type(meter_type)

        settings = await self._load_provider_settings(provider_name=provider_name)
        cache_key = self._cache_key(
            provider_name=provider_name,
            disco=normalized_disco,
            meter_number=normalized_meter_number,
        )

        cached = await self._get_cached_verification(cache_key, settings)
        if cached is not None:
            self.logger.info("electricity_meter_cache_hit", extra={"cache_key": cache_key})
            return cached

        self.logger.info(
            "electricity_meter_verification_started",
            extra={
                "provider_name": provider_name,
                "meter_hash": self._hash_value(normalized_meter_number),
                "disco": normalized_disco,
            },
        )

        try:
            response = self._normalize_provider_manager_response(
                result=await self.provider_manager.execute(
                    operation="purchase_electricity",
                    meter_number=normalized_meter_number,
                    provider=normalized_disco,
                    amount=0,
                    reference=f"meter-verify-{uuid4().hex[:12]}",
                ),
                meter_number=normalized_meter_number,
                disco=normalized_disco,
                meter_type=normalized_meter_type,
                provider_name=provider_name,
            )
        except NoProviderAvailableError as exc:
            raise ProviderException(detail=str(exc)) from exc
        except ProviderException:
            raise
        except Exception as exc:
            raise ProviderException(detail=str(exc)) from exc

        verification = self._build_verification_response(response, provider_name=provider_name)
        await self._cache_verification(cache_key, verification, settings)
        self.logger.info("electricity_meter_verification_succeeded", extra={"reference": verification["reference"], "provider_name": provider_name})
        self.logger.info("electricity_meter_verification_audit", extra={"event": "meter_verification", "reference": verification["reference"], "provider_name": provider_name, "disco": normalized_disco})
        return verification

    async def _load_provider_settings(self, *, provider_name: str | None) -> dict[str, Any]:
        provider_name_normalized = self._normalize_provider_name(provider_name)
        settings: dict[str, Any] = {
            "cache_enabled": await self._load_bool_setting("verification_cache_enabled", provider_name=provider_name_normalized, default=True),
            "cache_ttl_seconds": await self._load_int_setting("verification_cache_ttl", provider_name=provider_name_normalized, default=self.cache_ttl_seconds),
            "verification_enabled": await self._load_bool_setting("verification_enabled", provider_name=provider_name_normalized, default=True),
            "provider_specific": provider_name_normalized is not None,
        }
        if not settings["verification_enabled"]:
            raise ValidationException("Meter verification is disabled for the selected provider.")
        return settings

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValidationException("Meter verification payload is invalid.")
        if not payload.get("meter_number"):
            raise ValidationException("Meter number is required for meter verification.")
        if not payload.get("disco"):
            raise ValidationException("Distribution company is required for meter verification.")

    def _normalize_provider_manager_response(
        self,
        *,
        result: Any,
        meter_number: str,
        disco: str,
        meter_type: str | None,
        provider_name: str | None,
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ValidationException("Meter verification response must be a dictionary.")

        provider_value = self._coerce_string(result.get("provider"))
        payload = result.get("data") if isinstance(result.get("data"), dict) else result
        response_data: dict[str, Any] | Any = payload
        if isinstance(payload, dict):
            if isinstance(payload.get("verification"), dict):
                response_data = payload["verification"]
            elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("verification"), dict):
                response_data = payload["data"]["verification"]
            elif isinstance(payload.get("transaction_data"), dict):
                response_data = payload["transaction_data"]
            elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("transaction_data"), dict):
                response_data = payload["data"]["transaction_data"]

        normalized = {
            "customer_name": self._coerce_string(response_data.get("customer_name") or response_data.get("name") or response_data.get("account_name") if isinstance(response_data, dict) else None),
            "meter_number": meter_number,
            "meter_type": self._coerce_string(response_data.get("meter_type") or meter_type if isinstance(response_data, dict) else meter_type),
            "address": self._coerce_string(response_data.get("address") or response_data.get("customer_address") or response_data.get("location") if isinstance(response_data, dict) else None),
            "disco": disco,
            "provider_name": provider_name or provider_value or self._coerce_string(response_data.get("provider") if isinstance(response_data, dict) else None),
            "provider_reference": self._coerce_string(
                (response_data.get("reference") if isinstance(response_data, dict) else None)
                or (response_data.get("verification_reference") if isinstance(response_data, dict) else None)
                or (response_data.get("transaction_id") if isinstance(response_data, dict) else None)
                or result.get("provider_reference")
                or str(uuid4())
            ),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload": response_data,
        }

        self._assert_verification_required_fields(normalized)
        return normalized

    def _assert_verification_required_fields(self, normalized: dict[str, Any]) -> None:
        if not normalized["meter_number"]:
            raise ValidationException("Verified meter number is missing.")
        if not normalized["disco"]:
            raise ValidationException("Verified distribution company is missing.")

    def _build_verification_response(self, response: dict[str, Any], *, provider_name: str | None) -> dict[str, Any]:
        verification_reference = str(UUID(response.get("provider_reference"))) if self._is_valid_uuid(response.get("provider_reference")) else f"meter-{uuid4().hex[:12]}"
        return {
            "reference": verification_reference,
            "provider_name": provider_name or self._coerce_string(response.get("provider_name")),
            "customer_name": self._coerce_string(response.get("customer_name")),
            "meter_number": self._coerce_string(response.get("meter_number")),
            "meter_type": self._coerce_string(response.get("meter_type")),
            "address": self._coerce_string(response.get("address")),
            "disco": self._coerce_string(response.get("disco")),
            "verified_at": self._coerce_string(response.get("verified_at")),
            "provider_reference": self._coerce_string(response.get("provider_reference")),
            "raw_payload": response.get("raw_payload"),
        }

    async def _get_cached_verification(self, cache_key: str, settings: dict[str, Any]) -> dict[str, Any] | None:
        if self.redis_client is None or not settings.get("cache_enabled"):
            return None
        try:
            raw = await self.redis_client.get(cache_key)
            if not raw:
                return None
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
            return None
        except Exception as exc:
            self.logger.warning("electricity_meter_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_verification(self, cache_key: str, payload: dict[str, Any], settings: dict[str, Any]) -> None:
        if self.redis_client is None or not settings.get("cache_enabled"):
            return
        try:
            await self.redis_client.set(cache_key, json.dumps(payload), ex=settings.get("cache_ttl_seconds", self.cache_ttl_seconds))
            self.logger.info("electricity_meter_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("electricity_meter_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    def _cache_key(self, *, provider_name: str | None, disco: str, meter_number: str) -> str:
        provider_segment = self._normalize_provider_name(provider_name) or "default"
        meter_hash = self._hash_value(meter_number)
        disco_segment = disco.strip().lower()
        return self.CACHE_KEY_TEMPLATE.format(provider=provider_segment, disco=disco_segment, meter_hash=meter_hash)

    def _hash_value(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def _load_setting(self, key: str, provider_name: str | None = None) -> Any:
        if provider_name is not None:
            provider_key = self.PROVIDER_SETTING_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, provider=provider_name, key=key)
            record = await self.settings_repository.get_by_key(provider_key)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)

        default_key = self.DEFAULT_SETTING_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, key=key)
        record = await self.settings_repository.get_by_key(default_key)
        if record is not None and record.value is not None:
            return self._parse_setting_value(record.value, record.value_type)
        return None

    async def _load_bool_setting(self, key: str, provider_name: str | None = None, default: bool = False) -> bool:
        value = await self._load_setting(key, provider_name=provider_name)
        return self._coerce_bool(value, default=default)

    async def _load_int_setting(self, key: str, provider_name: str | None = None, default: int = 0) -> int:
        value = await self._load_setting(key, provider_name=provider_name)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return default

    def _parse_setting_value(self, value: str, value_type: str | None) -> Any:
        normalized_type = (value_type or "").strip().lower()
        if normalized_type == "json":
            try:
                return json.loads(value)
            except Exception:
                return None
        if normalized_type == "boolean":
            return self._coerce_bool(value)
        if normalized_type in {"int", "integer"}:
            try:
                return int(value)
            except Exception:
                return None
        if normalized_type in {"float", "decimal", "number", "numeric"}:
            try:
                return Decimal(str(value))
            except Exception:
                return None
        return value

    def _normalize_provider_name(self, provider_name: str | None) -> str | None:
        if provider_name is None:
            return None
        normalized = provider_name.strip().lower()
        return normalized or None

    def _normalize_meter_number(self, meter_number: str) -> str:
        if self.validation_service is not None:
            return self.validation_service.validate_meter_number(meter_number)
        if not meter_number or not isinstance(meter_number, str) or not meter_number.strip():
            raise ValidationException("Meter number is required.")
        normalized = meter_number.strip().upper().replace(" ", "")
        return normalized

    def _normalize_disco(self, disco: str) -> str:
        if self.validation_service is not None:
            return self.validation_service.validate_disco(disco)
        if not disco or not isinstance(disco, str) or not disco.strip():
            raise ValidationException("Distribution company is required.")
        return disco.strip().upper()

    def _normalize_meter_type(self, meter_type: str | None) -> str | None:
        if self.validation_service is not None:
            return self.validation_service.validate_meter_type(meter_type)
        if meter_type is None:
            return None
        if not isinstance(meter_type, str):
            raise ValidationException("Meter type must be a string.")
        return meter_type.strip() or None

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
        return default

    def _is_valid_uuid(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            UUID(value)
            return True
        except Exception:
            return False


__all__ = ["ElectricityMeterService"]
