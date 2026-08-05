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
from app.utils.exceptions import ProviderException, ValidationException


class EducationPricingService:
    """Calculate education service pricing using database-backed settings and optional provider overrides."""

    SETTINGS_PREFIX = "education_pricing"
    PROVIDER_KEY_TEMPLATE = "{prefix}:provider:{provider}:{key}"
    DEFAULT_KEY_TEMPLATE = "{prefix}:{key}"

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
        provider_name: str | None = None,
        examination_type: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Return a detailed education pricing breakdown for the requested amount."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(
            provider_name=provider_name,
            examination_type=examination_type,
            provider_operation=provider_operation,
        )
        self._validate_pricing_context(pricing_context)

        markup_amount = self._calculate_markup(amount_value, pricing_context)
        convenience_fee = self._calculate_rate(amount_value, pricing_context["convenience_fee_rate"])
        platform_fee = self._calculate_rate(amount_value, pricing_context["platform_fee_rate"])
        subtotal = amount_value + markup_amount + convenience_fee + platform_fee
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
            "examination_type": examination_type,
            "amount": amount_value.quantize(Decimal("0.01")),
            "markup_amount": markup_amount.quantize(Decimal("0.01")),
            "convenience_fee": convenience_fee.quantize(Decimal("0.01")),
            "platform_fee": platform_fee.quantize(Decimal("0.01")),
            "vat_amount": vat_amount.quantize(Decimal("0.01")),
            "promotion_discount": promotion_discount.quantize(Decimal("0.01")),
            "total_charges": (markup_amount + convenience_fee + platform_fee + vat_amount - promotion_discount).quantize(Decimal("0.01")),
            "total_amount": total_amount.quantize(Decimal("0.01")),
            "pricing_context": pricing_context,
            "provider_pricing_applied": bool(pricing_context.get("provider_specific")),
        }

        self.logger.info(
            "education_pricing_calculated",
            extra={
                "provider_name": provider_name,
                "examination_type": examination_type,
                "breakdown": {k: str(v) for k, v in breakdown.items() if isinstance(v, Decimal)},
            },
        )
        return breakdown

    async def _load_pricing_context(
        self,
        *,
        provider_name: str | None,
        examination_type: str | None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(provider_name=provider_name, examination_type=examination_type)
        cached = await self._get_cached_context(cache_key)
        if cached is not None:
            self.logger.info("education_pricing_cache_hit", extra={"cache_key": cache_key})
            return cached

        if provider_operation is not None:
            context = await self._load_provider_pricing_context(
                provider_operation=provider_operation,
                provider_name=provider_name,
                examination_type=examination_type,
            )
        else:
            context = {}

        if not context:
            context = await self._load_settings_context(provider_name=provider_name, examination_type=examination_type)

        self._validate_pricing_context(context)
        await self._cache_context(cache_key, context)
        return context

    async def _load_provider_pricing_context(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]],
        provider_name: str | None,
        examination_type: str | None,
    ) -> dict[str, Any]:
        try:
            response = await self.provider_service.execute_education(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "provider_name": provider_name,
                    "examination_type": examination_type,
                },
            )
        except Exception as exc:
            self.logger.warning(
                "education_pricing_provider_error",
                extra={"provider_name": provider_name, "examination_type": examination_type, "error": str(exc)},
            )
            return {}

        return self._normalize_provider_pricing_context(response, provider_name=provider_name, examination_type=examination_type)

    async def _load_settings_context(self, *, provider_name: str | None, examination_type: str | None) -> dict[str, Any]:
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
            "examination_type": examination_type,
        }
        return settings

    def _normalize_provider_pricing_context(
        self,
        response: dict[str, Any],
        provider_name: str | None,
        examination_type: str | None,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ValidationException("Provider pricing response is invalid.")

        return {
            "markup_type": self._coerce_string(response.get("markup_type") or "percentage").lower(),
            "markup_value": self._coerce_decimal(response.get("markup_value") or response.get("markup") or Decimal("0")),
            "vat_rate": self._coerce_decimal(response.get("vat_rate") or response.get("tax_rate") or Decimal("0")),
            "convenience_fee_rate": self._coerce_decimal(response.get("convenience_fee_rate") or response.get("convenience_fee") or Decimal("0")),
            "platform_fee_rate": self._coerce_decimal(response.get("platform_fee_rate") or response.get("platform_fee") or Decimal("0")),
            "promotions_enabled": self._coerce_bool(response.get("promotions_enabled"), default=True),
            "promotions": response.get("promotions") or [],
            "provider_specific": True,
            "provider_name": provider_name,
            "examination_type": examination_type,
        }

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
            raise ValidationException("Education pricing context is invalid.")

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
            self.logger.warning("education_pricing_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_context(self, cache_key: str, context: dict[str, Any]) -> None:
        if self.redis_client is None:
            return

        try:
            await self.redis_client.set(cache_key, json.dumps(self._serialize_context(context)), ex=self.cache_ttl_seconds)
            self.logger.info("education_pricing_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("education_pricing_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

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

    def _cache_key(self, *, provider_name: str | None, examination_type: str | None) -> str:
        parts = ["education:pricing"]
        if provider_name:
            parts.append(provider_name.strip().lower())
        if examination_type:
            parts.append(examination_type.strip().lower())
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
            return str(value).strip().lower() in {"true", "1", "yes"}
        if normalized_type in {"decimal", "numeric", "number"}:
            try:
                return Decimal(str(value))
            except Exception:
                return Decimal("0")
        return value

    def _validate_positive_amount(self, amount: Decimal | float | int) -> Decimal:
        amount_value = self._coerce_decimal(amount)
        if amount_value <= 0:
            raise ValidationException("Price must be positive.")
        return amount_value

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
            return value.strip().lower() in {"true", "1", "yes", "y"}
        return default

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _normalize_rate(self, value: Decimal) -> Decimal:
        if value is None:
            return Decimal("0")
        if value > Decimal("1"):
            return value / Decimal("100")
        return value

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, provider_response: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(provider_response, dict):
            return provider_response
        raise ValidationException("Provider response is invalid.")


__all__ = ["EducationPricingService"]
