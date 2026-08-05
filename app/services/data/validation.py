from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.wallet import Wallet
from app.services.wallet_service import WalletService
from app.utils.exceptions import AuthenticationException, ValidationException, WalletException


class DataValidationService:
    """Validate data purchase inputs without performing provider or database writes."""

    def __init__(self, *, wallet_service: WalletService | None = None) -> None:
        self.wallet_service = wallet_service

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID | None = None,
        phone_number: str | None = None,
        network: str | None = None,
        plan: Any | None = None,
        amount: Decimal | float | int | None = None,
        transaction_pin: str | None = None,
        wallet: Wallet | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Validate the core data-purchase request and return normalized values."""
        normalized_phone = self.normalize_phone_number(phone_number)
        network_name = self.validate_network(network)
        validated_plan = self.validate_plan(plan, network=network_name)
        if amount is not None:
            self.validate_amount(amount)
        wallet_record = await self.validate_wallet(wallet=wallet, wallet_id=wallet_id, user_id=user_id)
        await self.validate_transaction_pin(user_id=user_id, transaction_pin=transaction_pin, wallet=wallet_record)

        return {
            "phone_number": normalized_phone,
            "network": network_name,
            "plan": validated_plan,
            "wallet": wallet_record,
        }

    def validate_network(self, network: str | None) -> str:
        """Validate the supplied network against the supported operator list."""
        if not network or not isinstance(network, str) or not network.strip():
            raise ValidationException("Network is required.")

        normalized = network.strip().upper()
        mapping = {
            "MTN": "MTN",
            "AIRTEL": "AIRTEL",
            "GLO": "GLO",
            "9MOBILE": "9MOBILE",
            "9MOB": "9MOBILE",
            "9MOBILE": "9MOBILE",
        }
        supported = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
        if normalized not in supported:
            if normalized in {"AIRTEL", "AIRTEL"}:
                return "AIRTEL"
            if normalized in {"GLO", "GLO"}:
                return "GLO"
            if normalized in {"9MOBILE", "9MOB"}:
                return "9MOBILE"
            if normalized in {"MTN"}:
                return "MTN"
            raise ValidationException("Unsupported network operator.")
        return normalized

    def validate_phone_number(self, phone_number: str | None) -> str:
        """Validate and normalize a Nigerian mobile number for data purchases."""
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise ValidationException("Phone number is required.")
        normalized = self.normalize_phone_number(phone_number)
        if not re.fullmatch(r"\+?234\d{10}", normalized):
            raise ValidationException("Phone number must be a valid Nigerian number.")
        return normalized

    def validate_plan(self, plan: Any | None, *, network: str | None = None) -> dict[str, Any]:
        """Validate that a plan exists, is active, and matches the selected network."""
        if plan is None:
            raise ValidationException("Data plan is required.")

        if isinstance(plan, dict):
            normalized = dict(plan)
        else:
            normalized = {
                "id": getattr(plan, "id", None),
                "code": getattr(plan, "code", None),
                "name": getattr(plan, "name", None),
                "network": getattr(plan, "network", None),
                "status": getattr(plan, "status", None),
                "is_active": getattr(plan, "is_active", None),
                "price": getattr(plan, "price", None),
                "description": getattr(plan, "description", None),
                "validity_period": getattr(plan, "validity_period", None),
            }

        plan_id = normalized.get("id") or normalized.get("plan_id") or normalized.get("code") or normalized.get("bundle_code")
        if not plan_id:
            raise ValidationException("Data plan is invalid.")

        status_value = str(normalized.get("status") or "").strip().lower()
        active_flag = normalized.get("is_active")
        if active_flag is None:
            active_flag = status_value in {"active", "enabled", "available", "ready"}
        if not bool(active_flag):
            raise ValidationException("Data plan is not active.")

        if network is not None:
            plan_network = str(normalized.get("network") or "").strip().upper()
            if plan_network and plan_network != network.upper():
                raise ValidationException("Data plan does not match the selected network.")

        return normalized

    async def validate_wallet(self, *, wallet: Wallet | None, wallet_id: UUID | None, user_id: UUID | None) -> Wallet | dict[str, Any]:
        """Validate wallet existence, activity, and frozen or suspended state."""
        if wallet is not None:
            wallet_record = wallet
        elif wallet_id is not None:
            wallet_record = None
        elif self.wallet_service is not None and user_id is not None:
            wallet_data = await self.wallet_service.get_wallet_by_user(user_id=user_id)
            wallet_record = wallet_data.get("wallet") if isinstance(wallet_data, dict) else None
        else:
            wallet_record = None

        if wallet_record is None:
            raise ValidationException("Wallet not found.")

        if isinstance(wallet_record, dict):
            is_active = bool(wallet_record.get("is_active", True))
            is_frozen = bool(wallet_record.get("is_frozen", False))
            is_suspended = bool(wallet_record.get("is_suspended", False))
        else:
            is_active = bool(getattr(wallet_record, "is_active", True))
            is_frozen = bool(getattr(wallet_record, "is_frozen", False))
            is_suspended = bool(getattr(wallet_record, "is_suspended", False))

        if not is_active:
            raise WalletException("Wallet is not active.")
        if is_frozen or is_suspended:
            raise WalletException("Wallet is frozen or suspended.")
        return wallet_record

    async def validate_transaction_pin(self, *, user_id: UUID | None, transaction_pin: str | None, wallet: Wallet | None) -> None:
        """Validate that a transaction PIN is well-formed and correct when a wallet service is available."""
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")

        if self.wallet_service is None or user_id is None:
            return
        is_valid = await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)
        if not is_valid:
            raise AuthenticationException("Transaction PIN is invalid.")

    def normalize_phone_number(self, phone_number: str | None) -> str:
        """Normalize Nigerian phone numbers to the international form."""
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise ValidationException("Phone number is required.")
        value = phone_number.strip()
        if value.startswith("+234"):
            return value
        if value.startswith("234"):
            return f"+{value}"
        if value.startswith("0"):
            return f"+234{value[1:]}"
        return value

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Validate that a purchase amount is positive and within the supported range."""
        if amount is None:
            raise ValidationException("Amount is required.")
        amount_value = Decimal(str(amount))
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")
        if amount_value < Decimal("50"):
            raise ValidationException("Amount must be at least 50.")
        if amount_value > Decimal("50000"):
            raise ValidationException("Amount must not exceed 50000.")
        return amount_value.quantize(Decimal("0.01"))


__all__ = ["DataValidationService"]
