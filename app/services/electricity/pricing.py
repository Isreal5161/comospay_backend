from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis

from app.repositories.system_settings_repository import SystemSettingsRepository
from app.utils.exceptions import ValidationException


class ElectricityPricingService:
    """Calculate electricity pricing using database-backed configuration and cached settings."""

    SETTINGS_PREFIX = "electricity_pricing"
    PROVIDER_KEY_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    DEFAULT_KEY_TEMPLATE = "{prefix}:{key}"

    def __init__(
        self,
        *,
        settings_repository: SystemSettingsRepository,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.settings_repository = settings_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        provider_name: str | None = None,
        promotion_code: str | None = None,
        disco: str | None = None,
        meter_type: str | None = None,
    ) -> dict[str, Any]:
        """Return a detailed electricity pricing breakdown for the requested amount."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(
            provider_name=provider_name,
            disco=disco,
            meter_type=meter_type,
        )
        self._validate_pricing_context(pricing_context)

        markup_amount = self._calculate_markup(amount_value, pricing_context)
        convenience_fee = self._calculate_rate(amount_value, pricing_context["convenience_fee_rate"])
        platform_fee = self._calculate_rate(amount_value, pricing_context["platform_fee_rate"])
        subtotal_before_vat = amount_value + markup_amount + convenience_fee + platform_fee
        vat_amount = self._calculate_rate(subtotal_before_vat, pricing_context["vat_rate"])
        total_before_promotion = subtotal_before_vat + vat_amount
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
            "amount": amount_value.quantize(Decimal("0.01")),
            "markup_amount": markup_amount.quantize(Decimal("0.01")),
            "convenience_fee": convenience_fee.quantize(Decimal("0.01")),
            "platform_fee": platform_fee.quantize(Decimal("0.01")),
            "vat_amount": vat_amount.quantize(Decimal("0.01")),
            "promotion_discount": promotion_discount.quantize(Decimal("0.01")),
            "total_charges": (markup_amount + convenience_fee + platform_fee + vat_amount - promotion_discount).quantize(Decimal("0.01")),
            "total_amount": total_amount.quantize(Decimal("0.01")),
            "provider_pricing_applied": pricing_context["provider_specific"],
            "pricing_context": pricing_context,
        }

        self.logger.info("electricity_pricing_calculated", extra={"provider_name": provider_name, "breakdown": {k: str(v) for k, v in breakdown.items() if isinstance(v, Decimal)}})
        return breakdown

    async def _load_pricing_context(
        self,
        *,
        provider_name: str | None,
        disco: str | None,
        meter_type: str | None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(provider_name=provider_name, disco=disco, meter_type=meter_type)
        cached = await self._get_cached_context(cache_key)
        if cached is not None:
            self.logger.info("electricity_pricing_cache_hit", extra={"cache_key": cache_key})
            return cached

        provider_name_normalized = self._normalize_provider_name(provider_name)
        settings = {
            "markup_type": self._load_string_setting("markup_type", provider_name=provider_name_normalized) or "percentage",
            "markup_value": self._resolve_markup_value(provider_name=provider_name_normalized),
            "vat_rate": self._load_decimal_setting("vat_rate", provider_name=provider_name_normalized),
            "convenience_fee_rate": self._load_decimal_setting("convenience_fee_rate", provider_name=provider_name_normalized),
            "platform_fee_rate": self._load_decimal_setting("platform_fee_rate", provider_name=provider_name_normalized),
            "promotions_enabled": self._load_bool_setting("promotions_enabled", provider_name=provider_name_normalized),
            "promotions": self._load_json_setting("promotions", provider_name=provider_name_normalized) or [],
            "provider_specific": provider_name_normalized is not None,
            "provider_name": provider_name_normalized,
            "disco": disco,
            "meter_type": meter_type,
        }

        self._validate_pricing_context(settings)
        await self._cache_context(cache_key, settings)
        return settings

    def _resolve_markup_value(self, provider_name: str | None) -> Decimal:
        markup_value = self._load_decimal_setting("markup_value", provider_name=provider_name)
        if markup_value is not None and markup_value >= Decimal("0"):
            return markup_value

        fixed = self._load_decimal_setting("markup_fixed", provider_name=provider_name)
        percentage = self._load_decimal_setting("markup_percentage", provider_name=provider_name)

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
            markup_rate = self._normalize_rate(markup_value)
            markup = amount * markup_rate
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
        normalized_code = self._coerce_string(promotion_code).lower() if promotion_code else None
        if normalized_code:
            for item in promotions:
                if isinstance(item, dict) and self._coerce_string(item.get("code")).lower() == normalized_code:
                    return item
            return None
        for item in promotions:
            if isinstance(item, dict) and self._coerce_bool(item.get("enabled"), default=True):
                return item
        return None

    def _validate_pricing_context(self, context: dict[str, Any]) -> None:
        if not isinstance(context, dict):
            raise ValidationException("Electricity pricing context is invalid.")

        if self._coerce_decimal(context.get("markup_value")) < Decimal("0"):
            raise ValidationException("Markup value must be zero or greater.")
        if self._coerce_decimal(context.get("vat_rate")) < Decimal("0"):
            raise ValidationException("VAT rate must be zero or greater.")
        if self._coerce_decimal(context.get("convenience_fee_rate")) < Decimal("0"):
            raise ValidationException("Convenience fee rate must be zero or greater.")
        if self._coerce_decimal(context.get("platform_fee_rate")) < Decimal("0"):
            raise ValidationException("Platform fee rate must be zero or greater.")
        promotions = context.get("promotions")
        if promotions is not None and not isinstance(promotions, list):
            raise ValidationException("Promotions configuration must be a list.")

    def _validate_promotion(self, promotion: dict[str, Any]) -> None:
        if not isinstance(promotion, dict):
            raise ValidationException("Promotion payload is invalid.")

        now = datetime.now(timezone.utc)
        starts_at = self._coerce_datetime(promotion.get("starts_at"))
        ends_at = self._coerce_datetime(promotion.get("ends_at"))
        if starts_at is not None and now < starts_at:
            raise ValidationException("Promotion has not started yet.")
        if ends_at is not None and now > ends_at:
            raise ValidationException("Promotion has expired.")

        if self._coerce_decimal(promotion.get("discount_rate") or promotion.get("discount_value") or Decimal("0")) < Decimal("0"):
            raise ValidationException("Promotion discount values cannot be negative.")

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
            self.logger.warning("electricity_pricing_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_context(self, cache_key: str, context: dict[str, Any]) -> None:
        if self.redis_client is None:
            return

        try:
            await self.redis_client.set(cache_key, json.dumps(self._serialize_context(context)), ex=self.cache_ttl_seconds)
            self.logger.info("electricity_pricing_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("electricity_pricing_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

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

    def _cache_key(self, *, provider_name: str | None, disco: str | None, meter_type: str | None) -> str:
        parts = ["electricity:pricing"]
        if provider_name:
            parts.append(provider_name.strip().lower())
        if disco:
            parts.append(disco.strip().lower())
        if meter_type:
            parts.append(meter_type.strip().lower())
        return ":".join(parts)

    def _load_string_setting(self, key: str, provider_name: str | None = None) -> str | None:
        value = self._load_setting(key, provider_name=provider_name)
        if value is None:
            return None
        return str(value).strip()

    def _load_decimal_setting(self, key: str, provider_name: str | None = None) -> Decimal:
        value = self._load_setting(key, provider_name=provider_name)
        return self._coerce_decimal(value)

    def _load_bool_setting(self, key: str, provider_name: str | None = None) -> bool:
        value = self._load_setting(key, provider_name=provider_name)
        return self._coerce_bool(value)

    def _load_json_setting(self, key: str, provider_name: str | None = None) -> Any:
        raw = self._load_setting(key, provider_name=provider_name)
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

    def _load_setting(self, key: str, provider_name: str | None = None) -> Any:
        if provider_name is not None:
            provider_key = self.PROVIDER_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, provider=provider_name, key=key)
            record = self.settings_repository.get_by_key(provider_key)
            if record is not None and record.value is not None:
                return self._parse_setting_value(record.value, record.value_type)
        default_key = self.DEFAULT_KEY_TEMPLATE.format(prefix=self.SETTINGS_PREFIX, key=key)
        record = self.settings_repository.get_by_key(default_key)
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
        if normalized_type in {"decimal", "number", "numeric", "float", "int", "integer"}:
            return self._coerce_decimal(value)
        return value

    def _normalize_rate(self, rate: Decimal) -> Decimal:
        if rate > Decimal("1"):
            return rate / Decimal("100")
        return rate

    def _normalize_provider_name(self, provider_name: str | None) -> str | None:
        if provider_name is None:
            return None
        normalized = provider_name.strip().lower()
        return normalized or None

    def _validate_positive_amount(self, amount: Decimal | float | int) -> Decimal:
        amount_value = self._coerce_decimal(amount)
        if amount_value <= Decimal("0"):
            raise ValidationException("Amount must be greater than zero.")
        return amount_value.quantize(Decimal("0.01"))

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
        return str(value).strip()

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        return None


__all__ = ["ElectricityPricingService"]
