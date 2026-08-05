from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis

from app.repositories.system_settings_repository import SystemSettingsRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import ValidationException


class DataPricingService:
    """Calculate dynamic data pricing using provider data and cached configuration."""

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

    async def calculate_price(
        self,
        *,
        amount: Decimal | float | int,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Calculate the base price for a data plan from provider/configuration data."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        base_price = self._coerce_decimal(pricing_context.get("price") or amount_value)
        self.logger.info("data_pricing_calculated", extra={"amount": str(base_price), "network": network, "provider_name": provider_name})
        return base_price.quantize(Decimal("0.01"))

    async def calculate_discount(
        self,
        *,
        amount: Decimal | float | int,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Calculate the applicable discount against the current pricing context."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        discount_rate = self._coerce_decimal(pricing_context.get("discount_rate") or Decimal("0"))
        discount = amount_value * discount_rate
        self.logger.info("data_discount_calculated", extra={"amount": str(amount_value), "discount": str(discount)})
        return discount.quantize(Decimal("0.01"))

    async def calculate_cashback(
        self,
        *,
        amount: Decimal | float | int,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Calculate cashback for a pricing context when configured."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        cashback_rate = self._coerce_decimal(pricing_context.get("cashback_rate") or Decimal("0"))
        cashback = amount_value * cashback_rate
        self.logger.info("data_cashback_calculated", extra={"amount": str(amount_value), "cashback": str(cashback)})
        return cashback.quantize(Decimal("0.01"))

    async def calculate_commission(
        self,
        *,
        amount: Decimal | float | int,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Calculate reseller or platform commission for a pricing context."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        commission_rate = self._coerce_decimal(pricing_context.get("commission_rate") or Decimal("0"))
        commission = amount_value * commission_rate
        self.logger.info("data_commission_calculated", extra={"amount": str(amount_value), "commission": str(commission)})
        return commission.quantize(Decimal("0.01"))

    async def calculate_final_amount(
        self,
        *,
        amount: Decimal | float | int,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Calculate the final payable amount after discount and commission adjustments."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        base_price = self._coerce_decimal(pricing_context.get("price") or amount_value)
        discount = await self.calculate_discount(amount=base_price, plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        commission = await self.calculate_commission(amount=base_price, plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        final_amount = base_price - discount + commission
        self.logger.info("data_final_amount_calculated", extra={"base_price": str(base_price), "final_amount": str(final_amount)})
        return final_amount.quantize(Decimal("0.01"))

    async def apply_promotional_price(
        self,
        *,
        amount: Decimal | float | int,
        promotion_code: str | None = None,
        plan: dict[str, Any] | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
    ) -> Decimal:
        """Apply a validated promotional price if the promotion is active and within limits."""
        amount_value = self._validate_positive_amount(amount)
        pricing_context = await self._load_pricing_context(plan=plan, network=network, provider_name=provider_name, provider_operation=provider_operation)
        promotion = self._resolve_promotion(pricing_context, promotion_code=promotion_code)
        if promotion is None:
            return amount_value.quantize(Decimal("0.01"))

        self._validate_promotion(promotion)
        discounted_amount = amount_value * self._coerce_decimal(promotion.get("discount_rate") or Decimal("0"))
        self.logger.info("data_promotion_applied", extra={"promotion_code": promotion_code, "amount": str(amount_value), "discount": str(discounted_amount)})
        return (amount_value - discounted_amount).quantize(Decimal("0.01"))

    async def _load_pricing_context(
        self,
        *,
        plan: dict[str, Any] | None,
        network: str | None,
        provider_name: str | None,
        provider_operation: Any | None,
    ) -> dict[str, Any]:
        cache_key = self._cache_key(network=network, provider_name=provider_name, plan_id=self._extract_plan_id(plan))
        cached_context = await self._get_cached_context(cache_key)
        if cached_context is not None:
            self.logger.info("data_pricing_cache_hit", extra={"cache_key": cache_key})
            return cached_context

        if provider_operation is None:
            context = await self._load_settings_context(network=network, provider_name=provider_name, plan=plan)
        else:
            context = await self._load_provider_context(provider_operation=provider_operation, network=network, provider_name=provider_name, plan=plan)

        if not context:
            context = await self._load_settings_context(network=network, provider_name=provider_name, plan=plan)

        self._validate_pricing_context(context)
        await self._cache_context(cache_key, context)
        return context

    async def _load_settings_context(
        self,
        *,
        network: str | None,
        provider_name: str | None,
        plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        for key in ("data_pricing_default_price", "data_pricing_discount_rate", "data_pricing_cashback_rate", "data_pricing_commission_rate"):
            record = await self.settings_repository.get_by_key(key)
            if record is not None and record.value is not None:
                settings[key] = record.value
        if plan is not None:
            settings["price"] = plan.get("price") or plan.get("amount") or plan.get("cost")
            settings["network"] = plan.get("network") or network
            settings["provider_name"] = plan.get("provider_name") or provider_name
        return {
            "price": self._coerce_decimal(settings.get("data_pricing_default_price") or settings.get("price") or Decimal("0")),
            "discount_rate": self._coerce_decimal(settings.get("data_pricing_discount_rate") or Decimal("0")),
            "cashback_rate": self._coerce_decimal(settings.get("data_pricing_cashback_rate") or Decimal("0")),
            "commission_rate": self._coerce_decimal(settings.get("data_pricing_commission_rate") or Decimal("0")),
            "network": network,
            "provider_name": provider_name,
            "plan_id": self._extract_plan_id(plan),
            "promotions": [],
        }

    async def _load_provider_context(
        self,
        *,
        provider_operation: Any,
        network: str | None,
        provider_name: str | None,
        plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            response = await self.provider_service.execute_data(
                operation=provider_operation,
                validate=lambda payload: None,
                normalize=lambda result, provider: {"provider": provider.name, "plans": [result] if isinstance(result, dict) else []},
                payload={"network": network, "provider_name": provider_name, "plan": plan},
            )
        except Exception as exc:
            self.logger.warning("data_pricing_provider_error", extra={"error": str(exc)})
            return {}

        payload = response.get("plans") if isinstance(response, dict) else None
        plan_payload = payload[0] if isinstance(payload, list) and payload else None
        if plan_payload is None:
            return {}
        return {
            "price": self._coerce_decimal(plan_payload.get("price") or plan_payload.get("amount") or plan_payload.get("cost") or Decimal("0")),
            "discount_rate": self._coerce_decimal(plan_payload.get("discount_rate") or Decimal("0")),
            "cashback_rate": self._coerce_decimal(plan_payload.get("cashback_rate") or Decimal("0")),
            "commission_rate": self._coerce_decimal(plan_payload.get("commission_rate") or Decimal("0")),
            "network": plan_payload.get("network") or network,
            "provider_name": plan_payload.get("provider_name") or provider_name,
            "plan_id": self._extract_plan_id(plan_payload),
            "promotions": plan_payload.get("promotions") or [],
        }

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
            self.logger.warning("data_pricing_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    async def _cache_context(self, cache_key: str, context: dict[str, Any]) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.set(cache_key, json.dumps(context), ex=self.cache_ttl_seconds)
            self.logger.info("data_pricing_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("data_pricing_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    def _resolve_promotion(self, context: dict[str, Any], *, promotion_code: str | None) -> dict[str, Any] | None:
        promotions = context.get("promotions") or []
        if not isinstance(promotions, list):
            return None
        if not promotion_code:
            return promotions[0] if promotions else None
        for promotion in promotions:
            if isinstance(promotion, dict) and str(promotion.get("code") or "").lower() == str(promotion_code).lower():
                return promotion
        return None

    def _validate_promotion(self, promotion: dict[str, Any]) -> None:
        if not isinstance(promotion, dict):
            raise ValidationException("Promotion payload is invalid.")

        start_at = promotion.get("starts_at")
        end_at = promotion.get("ends_at")
        now = datetime.now(timezone.utc)
        if start_at and now < self._coerce_datetime(start_at):
            raise ValidationException("Promotion has not started yet.")
        if end_at and now > self._coerce_datetime(end_at):
            raise ValidationException("Promotion has expired.")

        discount_rate = self._coerce_decimal(promotion.get("discount_rate") or Decimal("0"))
        if discount_rate < 0:
            raise ValidationException("Promotion discount rate cannot be negative.")

    def _validate_pricing_context(self, context: dict[str, Any]) -> None:
        if not isinstance(context, dict):
            raise ValidationException("Pricing context is invalid.")
        if context.get("price") is None:
            raise ValidationException("Pricing context must include a price.")

    def _validate_positive_amount(self, amount: Decimal | float | int) -> Decimal:
        amount_value = self._coerce_decimal(amount)
        if amount_value <= 0:
            raise ValidationException("Price must be positive.")
        return amount_value

    def _cache_key(self, *, network: str | None, provider_name: str | None, plan_id: str | None) -> str:
        parts = ["data:pricing"]
        if network:
            parts.append(network.lower())
        if provider_name:
            parts.append(provider_name.lower())
        if plan_id:
            parts.append(plan_id.lower())
        return ":".join(parts)

    def _extract_plan_id(self, plan: dict[str, Any] | None) -> str | None:
        if not isinstance(plan, dict):
            return None
        return str(plan.get("id") or plan.get("plan_id") or plan.get("code") or plan.get("bundle_code") or "") or None

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

    def _coerce_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)


__all__ = ["DataPricingService"]
