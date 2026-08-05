from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from app.models.provider import Provider
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import GiftCardException, ValidationException


class GiftCardValuationService:
    """Calculate gift card valuation using provider and database-backed pricing rules."""

    SETTINGS_PREFIX = "giftcard_valuation"
    PROVIDER_KEY_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    BRAND_KEY_TEMPLATE = "{prefix}:brand:{brand}:{key}"
    COUNTRY_KEY_TEMPLATE = "{prefix}:country:{country}:{key}"
    CARD_TYPE_KEY_TEMPLATE = "{prefix}:card_type:{card_type}:{key}"
    DEFAULT_KEY_TEMPLATE = "{prefix}:{key}"
    CACHE_KEY_TEMPLATE = "giftcard:valuation:{provider_name}:{brand}:{country}:{card_type}"

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        settings_repository: SystemSettingsRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.provider_service = provider_service
        self.settings_repository = settings_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def calculate_valuation(
        self,
        *,
        amount: Decimal | float | int,
        brand: str,
        card_type: str,
        country: str | None = None,
        currency: str = "NGN",
        provider_name: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a standardized valuation for a gift card request."""
        amount_value = self._validate_amount(amount)
        brand_value = self._validate_string_field(brand, "Gift card brand")
        card_type_value = self._validate_string_field(card_type, "Gift card type")
        country_value = self._normalize_optional_string(country)
        currency_value = self._validate_string_field(currency, "Currency code")

        self.logger.info(
            "giftcard_valuation_started",
            extra={
                "brand": brand_value,
                "card_type": card_type_value,
                "country": country_value,
                "provider_name": provider_name,
                "amount": str(amount_value),
            },
        )

        valuation_context = await self._load_valuation_context(
            provider_name=provider_name,
            brand=brand_value,
            country=country_value,
            card_type=card_type_value,
            provider_operation=provider_operation,
        )

        base_rate = self._coerce_decimal(valuation_context.get("exchange_rate") or Decimal("1"))
        multiplier = self._coerce_decimal(valuation_context.get("card_type_multiplier") or Decimal("1"))
        country_multiplier = self._coerce_decimal(valuation_context.get("country_multiplier") or Decimal("1"))
        denomination_multiplier = self._coerce_decimal(valuation_context.get("denomination_multiplier") or Decimal("1"))
        markup_rate = self._coerce_decimal(valuation_context.get("markup_rate") or Decimal("0"))
        service_fee = self._coerce_decimal(valuation_context.get("service_fee") or Decimal("0"))

        base_valuation = amount_value * base_rate * multiplier * denomination_multiplier
        markup = base_valuation * markup_rate
        raw_valuation = base_valuation + markup

        promotion_discount = self._calculate_promotion_discount(
            raw_valuation,
            valuation_context,
            promotion_code=promotion_code,
        )

        net_valuation = raw_valuation - service_fee - promotion_discount
        if net_valuation < Decimal("0"):
            net_valuation = Decimal("0")

        result: dict[str, Any] = {
            "brand": brand_value,
            "card_type": card_type_value,
            "country": country_value,
            "currency": currency_value,
            "amount": amount_value.quantize(Decimal("0.01")),
            "exchange_rate": base_rate.quantize(Decimal("0.0001")),
            "card_type_multiplier": multiplier.quantize(Decimal("0.0001")),
            "country_multiplier": country_multiplier.quantize(Decimal("0.0001")),
            "denomination_multiplier": denomination_multiplier.quantize(Decimal("0.0001")),
            "markup_rate": markup_rate.quantize(Decimal("0.0001")),
            "service_fee": service_fee.quantize(Decimal("0.01")),
            "promotion_discount": promotion_discount.quantize(Decimal("0.01")),
            "raw_valuation": raw_valuation.quantize(Decimal("0.01")),
            "net_valuation": net_valuation.quantize(Decimal("0.01")),
            "provider_pricing_applied": bool(valuation_context.get("provider_pricing_applied")),
            "pricing_context": valuation_context,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        self.logger.info(
            "giftcard_valuation_completed",
            extra={
                "brand": brand_value,
                "card_type": card_type_value,
                "country": country_value,
                "net_valuation": str(result["net_valuation"]),
            },
        )

        return result

    async def _load_valuation_context(
        self,
        *,
        provider_name: str | None,
        brand: str,
        country: str | None,
        card_type: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(
            provider_name=provider_name,
            brand=brand,
            country=country,
            card_type=card_type,
        )

        cached = await self._get_cached_context(cache_key)
        if cached is not None:
            self.logger.info("giftcard_valuation_context_cache_hit", extra={"cache_key": cache_key})
            return cached

        provider_context: dict[str, Any] = {}
        if provider_operation is not None:
            provider_context = await self._load_provider_valuation_context(
                provider_operation=provider_operation,
                provider_name=provider_name,
                brand=brand,
                country=country,
                card_type=card_type,
            )

        settings_context = await self._load_settings_context(
            provider_name=provider_name,
            brand=brand,
            country=country,
            card_type=card_type,
        )

        context = {**settings_context, **provider_context}
        context["provider_pricing_applied"] = bool(provider_context)

        await self._cache_context(cache_key, context)
        return context

    async def _load_provider_valuation_context(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]],
        provider_name: str | None,
        brand: str,
        country: str | None,
        card_type: str,
    ) -> dict[str, Any]:
        try:
            response = await self.provider_service.execute_giftcard(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_valuation_response,
                payload={
                    "brand": brand,
                    "card_type": card_type,
                    "country": country,
                    "amount": str(brand),
                    "currency": brand,
                    "provider_name": provider_name,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "giftcard_valuation_provider_error",
                extra={"brand": brand, "card_type": card_type, "error": str(exc)},
            )
            return {}

        return self._normalize_provider_context(response)

    async def _load_settings_context(
        self,
        *,
        provider_name: str | None,
        brand: str,
        country: str | None,
        card_type: str,
    ) -> dict[str, Any]:
        return {
            "exchange_rate": await self._resolve_setting("exchange_rate", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "markup_rate": await self._resolve_setting("markup_rate", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "service_fee": await self._resolve_setting("service_fee", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "card_type_multiplier": await self._resolve_setting("card_type_multiplier", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "country_multiplier": await self._resolve_setting("country_multiplier", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "denomination_multiplier": await self._resolve_setting("denomination_multiplier", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "promotions_enabled": await self._resolve_setting("promotions_enabled", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
            "promotions": await self._resolve_setting("promotions", provider_name=provider_name, brand=brand, country=country, card_type=card_type),
        }

    def _normalize_provider_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {}
        if payload.get("exchange_rate") is not None:
            context["exchange_rate"] = self._coerce_decimal(payload["exchange_rate"])
        if payload.get("markup_rate") is not None:
            context["markup_rate"] = self._coerce_decimal(payload["markup_rate"])
        if payload.get("service_fee") is not None:
            context["service_fee"] = self._coerce_decimal(payload["service_fee"])
        if payload.get("card_type_multiplier") is not None:
            context["card_type_multiplier"] = self._coerce_decimal(payload["card_type_multiplier"])
        if payload.get("country_multiplier") is not None:
            context["country_multiplier"] = self._coerce_decimal(payload["country_multiplier"])
        if payload.get("denomination_multiplier") is not None:
            context["denomination_multiplier"] = self._coerce_decimal(payload["denomination_multiplier"])
        if payload.get("promotions_enabled") is not None:
            context["promotions_enabled"] = bool(payload["promotions_enabled"])
        if payload.get("promotions") is not None:
            context["promotions"] = payload["promotions"]
        return context

    def _normalize_provider_valuation_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}

        return {
            "provider": getattr(provider, "name", None) or self._coerce_string(payload.get("provider")),
            "exchange_rate": payload.get("exchange_rate"),
            "markup_rate": payload.get("markup_rate"),
            "service_fee": payload.get("service_fee"),
            "card_type_multiplier": payload.get("card_type_multiplier"),
            "country_multiplier": payload.get("country_multiplier"),
            "denomination_multiplier": payload.get("denomination_multiplier"),
            "promotions_enabled": payload.get("promotions_enabled"),
            "promotions": payload.get("promotions"),
        }

    async def _resolve_setting(
        self,
        key: str,
        *,
        provider_name: str | None,
        brand: str,
        country: str | None,
        card_type: str,
    ) -> Any:
        candidates: list[str] = []
        if provider_name is not None:
            candidates.append(self.PROVIDER_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, provider=provider_name, key=key))
        candidates.append(self.BRAND_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, brand=brand, key=key))
        if country is not None:
            candidates.append(self.COUNTRY_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, country=country, key=key))
        candidates.append(self.CARD_TYPE_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, card_type=card_type, key=key))
        candidates.append(self.DEFAULT_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, key=key))

        for candidate in candidates:
            record = await self.settings_repository.get_by_key(candidate)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)
        return None

    async def _get_cached_context(self, cache_key: str) -> dict[str, Any] | None:
        if self.redis_client is None:
            return None
        try:
            raw = await self.redis_client.get(cache_key)
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception as exc:
            self.logger.warning("giftcard_valuation_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_context(self, cache_key: str, context: dict[str, Any]) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.set(cache_key, json.dumps(self._serialize_context(context)), ex=self.cache_ttl_seconds)
            self.logger.info("giftcard_valuation_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("giftcard_valuation_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    def _cache_key(
        self,
        *,
        provider_name: str | None,
        brand: str,
        country: str | None,
        card_type: str,
    ) -> str:
        return self.CACHE_KEY_TEMPLATE.format(
            provider_name=self._normalize_optional_string(provider_name) or "default",
            brand=self._normalize_optional_string(brand) or "unknown",
            country=self._normalize_optional_string(country) or "any",
            card_type=self._normalize_optional_string(card_type) or "unknown",
        )

    def _serialize_context(self, context: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in context.items():
            if isinstance(value, Decimal):
                serialized[key] = str(value)
            elif isinstance(value, datetime):
                serialized[key] = value.isoformat()
            else:
                serialized[key] = value
        return serialized

    def _parse_setting_value(self, value: str, value_type: str | None) -> Any:
        normalized_type = (value_type or "").strip().lower()
        if normalized_type == "json":
            try:
                return json.loads(value)
            except Exception:
                return None
        if normalized_type == "boolean":
            return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}
        if normalized_type in {"int", "integer"}:
            try:
                return int(value)
            except Exception:
                return None
        if normalized_type in {"float", "decimal", "number", "numeric"}:
            try:
                return Decimal(str(value))
            except Exception:
                return Decimal("0")
        return value

    def _validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        if amount is None:
            raise ValidationException("Gift card amount is required.")
        try:
            amount_value = Decimal(str(amount))
        except Exception as exc:
            raise ValidationException("Gift card amount must be a valid number.") from exc
        if amount_value <= 0:
            raise ValidationException("Gift card amount must be greater than zero.")
        return amount_value

    def _validate_string_field(self, value: str | None, name: str) -> str:
        if not value or not isinstance(value, str) or not value.strip():
            raise ValidationException(f"{name} is required.")
        return value.strip()

    def _normalize_optional_string(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _coerce_decimal(self, value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value)
            except Exception:
                return Decimal("0")
        return Decimal("0")

    def _calculate_promotion_discount(
        self,
        amount: Decimal,
        context: dict[str, Any],
        promotion_code: str | None,
    ) -> Decimal:
        if not context.get("promotions_enabled"):
            return Decimal("0")
        promotions = context.get("promotions") or []
        promotion = self._resolve_promotion(promotions, promotion_code=promotion_code)
        if promotion is None:
            return Decimal("0")
        discount_type = str(promotion.get("discount_type", "percentage")).strip().lower()
        discount_value = self._coerce_decimal(promotion.get("discount_value") or promotion.get("discount_rate") or Decimal("0"))
        if discount_type == "fixed":
            discount = discount_value
        else:
            discount = amount * self._normalize_rate(discount_value)
        if discount < Decimal("0"):
            return Decimal("0")
        cap = self._coerce_decimal(promotion.get("maximum_discount") or promotion.get("cap") or Decimal("0"))
        if cap > Decimal("0") and discount > cap:
            discount = cap
        return min(discount, amount)

    def _resolve_promotion(self, promotions: Any, promotion_code: str | None) -> dict[str, Any] | None:
        if not isinstance(promotions, list):
            return None
        normalized_code = self._normalize_optional_string(promotion_code)
        if normalized_code:
            for item in promotions:
                if isinstance(item, dict) and str(item.get("code", "")).strip().lower() == normalized_code.lower():
                    return item
            return None
        for item in promotions:
            if isinstance(item, dict) and self._coerce_bool(item.get("enabled"), default=True):
                return item
        return None

    def _normalize_rate(self, rate: Decimal) -> Decimal:
        if rate < Decimal("0"):
            return Decimal("0")
        if rate > Decimal("1"):
            return rate / Decimal("100")
        return rate

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

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required for gift card valuation.")


__all__ = ["GiftCardValuationService"]
