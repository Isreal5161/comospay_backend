from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ValidationException, WalletException


class WalletLimitsService:
    """Validate and manage wallet transaction, daily, monthly, and balance limits."""

    DEFAULT_PER_TRANSACTION_LIMIT = Decimal("100000.00")
    DEFAULT_DAILY_LIMIT = Decimal("1000000.00")
    DEFAULT_MONTHLY_LIMIT = Decimal("10000000.00")
    DEFAULT_BALANCE_LIMIT = Decimal("50000000.00")

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        system_settings_repository: SystemSettingsRepository | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.system_settings_repository = system_settings_repository
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def validate_transaction_limit(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate whether an amount fits the per-transaction limit."""
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Transaction amount must be greater than zero.")

        limits = await self.get_user_limits(user_id=user_id, wallet_id=wallet_id)
        per_transaction_limit = self._normalize_amount(limits["per_transaction_limit"])
        if amount_value > per_transaction_limit:
            raise ValidationException("Transaction exceeds the per-transaction limit.")
        return {
            "valid": True,
            "per_transaction_limit": str(per_transaction_limit),
            "remaining": str(per_transaction_limit - amount_value),
        }

    async def validate_daily_limit(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate whether an amount fits the daily limit for a user and wallet."""
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")

        limits = await self.get_user_limits(user_id=user_id, wallet_id=wallet_id)
        daily_limit = self._normalize_amount(limits["daily_limit"])
        if amount_value > daily_limit:
            raise ValidationException("Transaction exceeds the daily limit.")
        return {
            "valid": True,
            "daily_limit": str(daily_limit),
            "remaining": str(daily_limit - amount_value),
        }

    async def validate_monthly_limit(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate whether an amount fits the monthly limit for a user and wallet."""
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")

        limits = await self.get_user_limits(user_id=user_id, wallet_id=wallet_id)
        monthly_limit = self._normalize_amount(limits["monthly_limit"])
        if amount_value > monthly_limit:
            raise ValidationException("Transaction exceeds the monthly limit.")
        return {
            "valid": True,
            "monthly_limit": str(monthly_limit),
            "remaining": str(monthly_limit - amount_value),
        }

    async def validate_wallet_balance_limit(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate that a transaction will not push the wallet above the configured balance limit."""
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")

        wallet = await self._get_wallet(wallet_id=wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.user_id != user_id:
            raise WalletException("Wallet ownership mismatch.")

        limits = await self.get_user_limits(user_id=user_id, wallet_id=wallet_id)
        balance_limit = self._normalize_amount(limits["balance_limit"])
        projected_balance = wallet.available_balance + amount_value
        if projected_balance > balance_limit:
            raise ValidationException("Wallet balance exceeds the configured balance limit.")
        return {
            "valid": True,
            "balance_limit": str(balance_limit),
            "projected_balance": str(projected_balance),
            "remaining": str(balance_limit - projected_balance),
        }

    async def get_user_limits(self, *, user_id: UUID, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Return effective wallet limits for a user and optional wallet."""
        self._require_repository(self.user_repository)
        self._require_repository(self.wallet_repository)

        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User not found.")

        wallet = await self._get_wallet(wallet_id=wallet_id) if wallet_id is not None else None
        if wallet_id is not None and wallet is None:
            raise ValidationException("Wallet not found.")

        if wallet is not None and wallet.user_id != user_id:
            raise WalletException("Wallet ownership mismatch.")

        settings = await self._load_settings()
        level = getattr(user, "kyc_level", None) or "basic"
        per_transaction_limit = self._coerce_limit(settings.get("wallet_per_transaction_limit"), self.DEFAULT_PER_TRANSACTION_LIMIT)
        daily_limit = self._coerce_limit(settings.get("wallet_daily_limit"), self.DEFAULT_DAILY_LIMIT)
        monthly_limit = self._coerce_limit(settings.get("wallet_monthly_limit"), self.DEFAULT_MONTHLY_LIMIT)
        balance_limit = self._coerce_limit(settings.get("wallet_balance_limit"), self.DEFAULT_BALANCE_LIMIT)

        if level in {"verified", "vip", "premium"}:
            per_transaction_limit = max(per_transaction_limit, self.DEFAULT_PER_TRANSACTION_LIMIT * Decimal("2"))
            daily_limit = max(daily_limit, self.DEFAULT_DAILY_LIMIT * Decimal("2"))
            monthly_limit = max(monthly_limit, self.DEFAULT_MONTHLY_LIMIT * Decimal("2"))
            balance_limit = max(balance_limit, self.DEFAULT_BALANCE_LIMIT * Decimal("2"))

        return {
            "user_id": str(user_id),
            "wallet_id": str(wallet.id) if wallet is not None else None,
            "kyc_level": level,
            "per_transaction_limit": str(per_transaction_limit),
            "daily_limit": str(daily_limit),
            "monthly_limit": str(monthly_limit),
            "balance_limit": str(balance_limit),
        }

    async def update_user_limits(
        self,
        *,
        user_id: UUID,
        per_transaction_limit: Decimal | float | int | None = None,
        daily_limit: Decimal | float | int | None = None,
        monthly_limit: Decimal | float | int | None = None,
        balance_limit: Decimal | float | int | None = None,
    ) -> dict[str, Any]:
        """Persist updated wallet limits into system settings when available."""
        self._require_repository(self.user_repository)
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User not found.")

        values: dict[str, Decimal] = {}
        if per_transaction_limit is not None:
            values["wallet_per_transaction_limit"] = self._normalize_amount(per_transaction_limit)
        if daily_limit is not None:
            values["wallet_daily_limit"] = self._normalize_amount(daily_limit)
        if monthly_limit is not None:
            values["wallet_monthly_limit"] = self._normalize_amount(monthly_limit)
        if balance_limit is not None:
            values["wallet_balance_limit"] = self._normalize_amount(balance_limit)

        if not values:
            raise ValidationException("At least one limit value must be provided.")

        if self.system_settings_repository is None:
            return await self.get_user_limits(user_id=user_id, wallet_id=None)

        for key, value in values.items():
            setting = await self.system_settings_repository.get_by_key(key)
            if setting is None:
                setting = SystemSettings(key=key, value=str(value), value_type="decimal", category="wallet", description=f"Wallet {key} limit")
                await self.system_settings_repository.create_setting(setting)
            else:
                await self.system_settings_repository.update_setting(setting.id, value=str(value), is_active=True)

        return await self.get_user_limits(user_id=user_id, wallet_id=None)

    async def reset_daily_limits(self, *, user_id: UUID, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Reset the daily-limit tracking surface for a user and wallet."""
        await self._ensure_user_and_wallet(user_id=user_id, wallet_id=wallet_id)
        return {"status": "reset", "user_id": str(user_id), "wallet_id": str(wallet_id) if wallet_id else None}

    async def reset_monthly_limits(self, *, user_id: UUID, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Reset the monthly-limit tracking surface for a user and wallet."""
        await self._ensure_user_and_wallet(user_id=user_id, wallet_id=wallet_id)
        return {"status": "reset", "user_id": str(user_id), "wallet_id": str(wallet_id) if wallet_id else None}

    async def calculate_remaining_limits(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Return all remaining limit values after applying a proposed amount."""
        amount_value = self._normalize_amount(amount)
        limits = await self.get_user_limits(user_id=user_id, wallet_id=wallet_id)
        return {
            "per_transaction_remaining": str(self._normalize_amount(limits["per_transaction_limit"]) - amount_value),
            "daily_remaining": str(self._normalize_amount(limits["daily_limit"]) - amount_value),
            "monthly_remaining": str(self._normalize_amount(limits["monthly_limit"]) - amount_value),
            "balance_remaining": str(self._normalize_amount(limits["balance_limit"]) - amount_value),
        }

    async def check_limit_status(self, *, user_id: UUID, wallet_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Evaluate whether the requested amount is permitted under the configured limits."""
        amount_value = self._normalize_amount(amount)
        await self.validate_transaction_limit(user_id=user_id, wallet_id=wallet_id, amount=amount_value)
        await self.validate_daily_limit(user_id=user_id, wallet_id=wallet_id, amount=amount_value)
        await self.validate_monthly_limit(user_id=user_id, wallet_id=wallet_id, amount=amount_value)
        await self.validate_wallet_balance_limit(user_id=user_id, wallet_id=wallet_id, amount=amount_value)
        return {"valid": True, "amount": str(amount_value)}

    async def _ensure_user_and_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> None:
        self._require_repository(self.user_repository)
        self._require_repository(self.wallet_repository)
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User not found.")
        if wallet_id is None:
            return
        wallet = await self._get_wallet(wallet_id=wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.user_id != user_id:
            raise WalletException("Wallet ownership mismatch.")

    async def _get_wallet(self, *, wallet_id: UUID | None) -> Any | None:
        if wallet_id is None:
            return None
        return await self.wallet_repository.get_by_id(wallet_id)

    async def _load_settings(self) -> dict[str, Decimal]:
        if self.system_settings_repository is None:
            return {}
        settings: dict[str, Decimal] = {}
        for key in [
            "wallet_per_transaction_limit",
            "wallet_daily_limit",
            "wallet_monthly_limit",
            "wallet_balance_limit",
        ]:
            setting = await self.system_settings_repository.get_by_key(key)
            if setting is not None and getattr(setting, "value", None) is not None:
                try:
                    settings[key] = Decimal(str(setting.value))
                except Exception:
                    continue
        return settings

    def _coerce_limit(self, value: Decimal | str | None, default: Decimal) -> Decimal:
        if value is None:
            return default
        try:
            return self._normalize_amount(value)
        except Exception:
            return default

    def _normalize_amount(self, amount: Decimal | float | int | str) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletLimitsService.")
