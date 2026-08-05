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
from app.utils.exceptions import ValidationException


class GiftCardPricingService:
    """Calculate gift card pricing using provider and database-backed settings."""

    SETTINGS_PREFIX = "giftcard_pricing"
    PROVIDER_KEY_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    BRAND_KEY_TEMPLATE = "{prefix}:brand:{brand}:{key}"
    COUNTRY_KEY_TEMPLATE = "{prefix}:country:{country}:{key}"
    DENOMINATION_KEY_TEMPLATE = "{prefix}:denomination:{denomination}:{key}"
    CARD_TYPE_KEY_TEMPLATE = "{prefix}:card_type:{card_type}:{key}"
    CARD_FORMAT_KEY_TEMPLATE = "{prefix}:format:{card_format}:{key}"
    DEFAULT_KEY_TEMPLATE = "{prefix}:{key}"
    CACHE_KEY_TEMPLATE = "giftcard:pricing:{provider_name}:{brand}:{country}:{denomination}:{card_type}:{card_format}"

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

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        brand: str,
        card_type: str,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
        currency: str = "NGN",
        provider_name: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a gift card pricing breakdown for the requested card details."""
        amount_value = self._validate_positive_amount(amount)
        brand_value = self._validate_string_field(brand, "Gift card brand")
        card_type_value = self._validate_string_field(card_type, "Gift card type")
        country_value = self._normalize_optional_string(country)
        denomination_value = self._normalize_optional_string(denomination)
        card_format_value = self._normalize_optional_string(card_format)
        currency_value = self._validate_string_field(currency, "Currency code")

        self.logger.info(
            "giftcard_pricing_started",
            extra={
                "brand": brand_value,
                "card_type": card_type_value,
                "country": country_value,
                "denomination": denomination_value,
                "card_format": card_format_value,
                "provider_name": provider_name,
                "amount": str(amount_value),
            },
        )

        pricing_context = await self._load_pricing_context(
            provider_name=provider_name,
            brand=brand_value,
            card_type=card_type_value,
            country=country_value,
            denomination=denomination_value,
            card_format=card_format_value,
            provider_operation=provider_operation,
        )
        self._validate_pricing_context(pricing_context)

        markup_amount = self._calculate_markup(amount_value, pricing_context)
        convenience_fee = self._calculate_rate(amount_value, pricing_context["convenience_fee_rate"])
        platform_fee = self._calculate_rate(amount_value, pricing_context["platform_fee_rate"])
        service_fee = self._coerce_decimal(pricing_context.get("service_fee") or Decimal("0"))
        subtotal = amount_value + markup_amount + convenience_fee + platform_fee + service_fee
        vat_amount = self._calculate_rate(subtotal, pricing_context["vat_rate"])
        total_before_promotion = subtotal + vat_amount
        promotion_discount = self._calculate_promotion_discount(
            total_before_promotion,
            pricing_context,
            promotion_code=promotion_code,
        )
        total_amount = total_before_promotion - promotion_discount
        if total_amount < Decimal("0"):
            total_amount = Decimal("0")

        breakdown = {
            "provider_name": provider_name,
            "brand": brand_value,
            "card_type": card_type_value,
            "country": country_value,
            "denomination": denomination_value,
            "card_format": card_format_value,
            "currency": currency_value,
            "amount": amount_value.quantize(Decimal("0.01")),
            "markup_amount": markup_amount.quantize(Decimal("0.01")),
            "convenience_fee": convenience_fee.quantize(Decimal("0.01")),
            "platform_fee": platform_fee.quantize(Decimal("0.01")),
            "service_fee": service_fee.quantize(Decimal("0.01")),
            "vat_amount": vat_amount.quantize(Decimal("0.01")),
            "promotion_discount": promotion_discount.quantize(Decimal("0.01")),
            "total_charges": (markup_amount + convenience_fee + platform_fee + service_fee + vat_amount - promotion_discount).quantize(Decimal("0.01")),
            "total_amount": total_amount.quantize(Decimal("0.01")),
            "provider_pricing_applied": bool(pricing_context.get("provider_specific")),
            "pricing_context": pricing_context,
        }

        self.logger.info(
            "giftcard_pricing_completed",
            extra={
                "brand": brand_value,
                "card_type": card_type_value,
                "total_amount": str(total_amount),
            },
        )
        return breakdown

    async def _load_pricing_context(
        self,
        *,
        provider_name: str | None,
        brand: str,
        card_type: str,
        country: str | None,
        denomination: str | None,
        card_format: str | None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        cached = await self._get_cached_context(cache_key)
        if cached is not None:
            self.logger.info("giftcard_pricing_cache_hit", extra={"cache_key": cache_key})
            return cached

        context: dict[str, Any] = {}
        if provider_operation is not None:
            context = await self._load_provider_pricing_context(
                provider_operation=provider_operation,
                provider_name=provider_name,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            )

        if not context:
            context = await self._load_settings_context(
                provider_name=provider_name,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            )

        self._validate_pricing_context(context)
        await self._cache_context(cache_key, context)
        return context

    async def _load_provider_pricing_context(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]],
        provider_name: str | None,
        brand: str,
        card_type: str,
        country: str | None,
        denomination: str | None,
        card_format: str | None,
    ) -> dict[str, Any]:
        try:
            response = await self.provider_service.execute_giftcard(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "provider_name": provider_name,
                    "brand": brand,
                    "card_type": card_type,
                    "country": country,
                    "denomination": denomination,
                    "card_format": card_format,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "giftcard_pricing_provider_error",
                extra={
                    "provider_name": provider_name,
                    "brand": brand,
                    "card_type": card_type,
                    "error": str(exc),
                },
            )
            return {}

        return self._normalize_provider_pricing_context(response, provider_name=provider_name, brand=brand, card_type=card_type, country=country, denomination=denomination, card_format=card_format)

    async def _load_settings_context(
        self,
        *,
        provider_name: str | None,
        brand: str,
        card_type: str,
        country: str | None,
        denomination: str | None,
        card_format: str | None,
    ) -> dict[str, Any]:
        provider_name_normalized = self._normalize_provider_name(provider_name)
        return {
            "markup_type": await self._load_string_setting(
                "markup_type",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ) or "percentage",
            "markup_value": await self._resolve_markup_value(
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "vat_rate": await self._load_decimal_setting(
                "vat_rate",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "convenience_fee_rate": await self._load_decimal_setting(
                "convenience_fee_rate",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "platform_fee_rate": await self._load_decimal_setting(
                "platform_fee_rate",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "service_fee": await self._load_decimal_setting(
                "service_fee",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "promotions_enabled": await self._load_bool_setting(
                "promotions_enabled",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ),
            "promotions": await self._load_json_setting(
                "promotions",
                provider_name=provider_name_normalized,
                brand=brand,
                card_type=card_type,
                country=country,
                denomination=denomination,
                card_format=card_format,
            ) or [],
            "provider_specific": provider_name_normalized is not None,
            "provider_name": provider_name_normalized,
            "brand": brand,
            "card_type": card_type,
            "country": country,
            "denomination": denomination,
            "card_format": card_format,
        }

    def _normalize_provider_pricing_context(
        self,
        response: dict[str, Any],
        provider_name: str | None,
        brand: str,
        card_type: str,
        country: str | None,
        denomination: str | None,
        card_format: str | None,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ValidationException("Provider pricing response is invalid.")

        return {
            "markup_type": self._coerce_string(response.get("markup_type") or "percentage").lower(),
            "markup_value": self._coerce_decimal(response.get("markup_value") or response.get("markup") or Decimal("0")),
            "vat_rate": self._coerce_decimal(response.get("vat_rate") or response.get("tax_rate") or Decimal("0")),
            "convenience_fee_rate": self._coerce_decimal(response.get("convenience_fee_rate") or response.get("convenience_fee") or Decimal("0")),
            "platform_fee_rate": self._coerce_decimal(response.get("platform_fee_rate") or response.get("platform_fee") or Decimal("0")),
            "service_fee": self._coerce_decimal(response.get("service_fee") or Decimal("0")),
            "promotions_enabled": self._coerce_bool(response.get("promotions_enabled"), default=True),
            "promotions": response.get("promotions") or [],
            "provider_specific": True,
            "provider_name": provider_name,
            "brand": brand,
            "card_type": card_type,
            "country": country,
            "denomination": denomination,
            "card_format": card_format,
        }

    async def _resolve_markup_value(
        self,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> Decimal:
        markup_value = await self._load_decimal_setting(
            "markup_value",
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        if markup_value is not None and markup_value >= Decimal("0"):
            return markup_value

        fixed = await self._load_decimal_setting(
            "markup_fixed",
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        percentage = await self._load_decimal_setting(
            "markup_percentage",
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )

        if fixed is not None and fixed >= Decimal("0"):
            return fixed
        if percentage is not None and percentage >= Decimal("0"):
            return percentage
        return Decimal("0")

    def _calculate_markup(self, amount: Decimal, context: dict[str, Any]) -> Decimal:
        markup_type = self._coerce_string(context.get("markup_type") or "percentage").lower()
        markup_value = self._coerce_decimal(context.get("markup_value") or Decimal("0"))
        if markup_type == "fixed":
            markup = markup_value
        else:
            markup = amount * self._normalize_rate(markup_value)
        if markup < Decimal("0"):
            raise ValidationException("Markup cannot be negative.")
        return markup

    def _calculate_rate(self, amount: Decimal, rate: Decimal) -> Decimal:
        rate_value = self._normalize_rate(self._coerce_decimal(rate))
        if rate_value <= Decimal("0"):
            return Decimal("0")
        result = amount * rate_value
        if result < Decimal("0"):
            raise ValidationException("Calculated fee cannot be negative.")
        return result

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

        self._validate_promotion(promotion)
        discount_type = self._coerce_string(promotion.get("discount_type") or "percentage").lower()
        if discount_type == "fixed":
            discount = self._coerce_decimal(promotion.get("discount_value") or promotion.get("amount") or Decimal("0"))
        else:
            rate = self._normalize_rate(self._coerce_decimal(promotion.get("discount_rate") or Decimal("0")))
            discount = amount * rate

        maximum = self._coerce_decimal(promotion.get("maximum_discount") or promotion.get("cap") or Decimal("0"))
        if maximum > Decimal("0") and discount > maximum:
            discount = maximum

        if discount < Decimal("0"):
            raise ValidationException("Promotion discount cannot be negative.")
        return min(discount, amount)

    def _resolve_promotion(self, promotions: Any, promotion_code: str | None) -> dict[str, Any] | None:
        if not isinstance(promotions, list):
            return None
        normalized_code = self._normalize_optional_string(promotion_code)
        if normalized_code:
            for item in promotions:
                if isinstance(item, dict) and self._coerce_string(item.get("code")).lower() == normalized_code.lower():
                    return item
            return None
        for item in promotions:
            if isinstance(item, dict) and self._coerce_bool(item.get("enabled"), default=True):
                return item
        return None

    def _validate_pricing_context(self, context: dict[str, Any]) -> None:
        if not isinstance(context, dict):
            raise ValidationException("Gift card pricing context is invalid.")
        if self._coerce_decimal(context.get("markup_value")) < Decimal("0"):
            raise ValidationException("Markup value must be zero or greater.")
        if self._coerce_decimal(context.get("vat_rate")) < Decimal("0"):
            raise ValidationException("VAT rate must be zero or greater.")
        if self._coerce_decimal(context.get("convenience_fee_rate")) < Decimal("0"):
            raise ValidationException("Convenience fee rate must be zero or greater.")
        if self._coerce_decimal(context.get("platform_fee_rate")) < Decimal("0"):
            raise ValidationException("Platform fee rate must be zero or greater.")
        if self._coerce_decimal(context.get("service_fee")) < Decimal("0"):
            raise ValidationException("Service fee cannot be negative.")
        promotions = context.get("promotions")
        if promotions is not None and not isinstance(promotions, list):
            raise ValidationException("Promotions configuration must be a list.")

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
            self.logger.warning("giftcard_pricing_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_context(self, cache_key: str, context: dict[str, Any]) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.set(cache_key, json.dumps(self._serialize_context(context)), ex=self.cache_ttl_seconds)
            self.logger.info("giftcard_pricing_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("giftcard_pricing_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

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

    def _cache_key(
        self,
        *,
        provider_name: str | None,
        brand: str,
        card_type: str,
        country: str | None,
        denomination: str | None,
        card_format: str | None,
    ) -> str:
        return self.CACHE_KEY_TEMPLATE.format(
            provider_name=self._normalize_optional_string(provider_name) or "default",
            brand=self._normalize_optional_string(brand) or "unknown",
            country=self._normalize_optional_string(country) or "any",
            denomination=self._normalize_optional_string(denomination) or "any",
            card_type=self._normalize_optional_string(card_type) or "unknown",
            card_format=self._normalize_optional_string(card_format) or "any",
        )

    async def _load_string_setting(
        self,
        key: str,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> str | None:
        value = await self._load_setting(
            key,
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        if value is None:
            return None
        return str(value).strip()

    async def _load_decimal_setting(
        self,
        key: str,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> Decimal:
        value = await self._load_setting(
            key,
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        return self._coerce_decimal(value)

    async def _load_bool_setting(
        self,
        key: str,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> bool:
        value = await self._load_setting(
            key,
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        return self._coerce_bool(value)

    async def _load_json_setting(
        self,
        key: str,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> Any:
        raw = await self._load_setting(
            key,
            provider_name=provider_name,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
        )
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return None
        return None

    async def _load_setting(
        self,
        key: str,
        *,
        provider_name: str | None = None,
        brand: str | None = None,
        card_type: str | None = None,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
    ) -> Any:
        if provider_name is not None:
            provider_key = self.PROVIDER_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, provider=provider_name, key=key)
            record = await self.settings_repository.get_by_key(provider_key)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)

        candidates = [
            self.BRAND_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, brand=brand or "", key=key),
            self.COUNTRY_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, country=country or "", key=key),
            self.DENOMINATION_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, denomination=denomination or "", key=key),
            self.CARD_TYPE_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, card_type=card_type or "", key=key),
            self.CARD_FORMAT_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, card_format=card_format or "", key=key),
            self.DEFAULT_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, key=key),
        ]
        for candidate in candidates:
            record = await self.settings_repository.get_by_key(candidate)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)
        return None

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
                return Decimal("0")
        return value

    def _normalize_provider_name(self, provider_name: str | None) -> str | None:
        if provider_name is None:
            return None
        normalized = provider_name.strip().lower()
        return normalized or None

    def _validate_positive_amount(self, amount: Decimal | float | int) -> Decimal:
        amount_value = self._coerce_decimal(amount)
        if amount_value <= Decimal("0"):
            raise ValidationException("Amount must be greater than zero.")
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

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value)

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        return None

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required for gift card pricing.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": str(payload.get("status") or "success").lower(),
            "provider": getattr(provider, "name", None),
            "provider_reference": self._coerce_string(payload.get("provider_reference") or payload.get("reference") or payload.get("id")),
            "message": self._coerce_string(payload.get("message") or payload.get("detail")),
            "raw": payload,
        }

    def _validate_promotion(self, promotion: dict[str, Any]) -> None:
        if not isinstance(promotion, dict):
            raise ValidationException("Promotion configuration is invalid.")
        if self._coerce_string(promotion.get("discount_type") or "percentage").lower() not in {"fixed", "percentage"}:
            raise ValidationException("Promotion discount type is invalid.")


__all__ = ["GiftCardPricingService"]
