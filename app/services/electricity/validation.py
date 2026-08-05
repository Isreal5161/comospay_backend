from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.wallet import Wallet
from app.services.wallet_service import WalletService
from app.utils.exceptions import AuthenticationException, ValidationException, WalletException


class ElectricityValidationService:
    """Validate electricity purchase inputs without performing provider or database writes."""

    def __init__(self, *, wallet_service: WalletService | None = None) -> None:
        self.wallet_service = wallet_service

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID,
        meter_number: str,
        disco: str,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
        meter_type: str | None = None,
        customer_name: str | None = None,
        wallet: Wallet | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Validate the core electricity purchase request and return normalized values."""
        normalized_meter_number = self.validate_meter_number(meter_number)
        normalized_disco = self.validate_disco(disco)
        normalized_meter_type = self.validate_meter_type(meter_type)
        amount_value = self.validate_amount(amount)
        wallet_record = await self.validate_wallet(wallet=wallet, wallet_id=wallet_id, user_id=user_id)
        await self.validate_transaction_pin(user_id=user_id, transaction_pin=transaction_pin, wallet=wallet_record)

        return {
            "meter_number": normalized_meter_number,
            "disco": normalized_disco,
            "meter_type": normalized_meter_type,
            "customer_name": self.normalize_customer_name(customer_name),
            "amount": amount_value,
            "wallet": wallet_record,
        }

    def validate_disco(self, disco: str | None) -> str:
        """Validate and normalize the distribution company identifier."""
        if not disco or not isinstance(disco, str) or not disco.strip():
            raise ValidationException("Distribution company is required.")
        normalized = re.sub(r"\s+", " ", disco.strip()).upper()
        if len(normalized) > 100:
            raise ValidationException("Distribution company is too long.")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9 _.-]{1,99}", normalized):
            raise ValidationException("Distribution company contains invalid characters.")
        return normalized

    def validate_meter_number(self, meter_number: str | None) -> str:
        """Validate and normalize the customer meter number."""
        if not meter_number or not isinstance(meter_number, str) or not meter_number.strip():
            raise ValidationException("Meter number is required.")
        normalized = re.sub(r"\s+", "", meter_number.strip()).upper()
        if len(normalized) < 4 or len(normalized) > 50:
            raise ValidationException("Meter number must be between 4 and 50 characters.")
        if not re.fullmatch(r"[A-Z0-9-]+", normalized):
            raise ValidationException("Meter number contains invalid characters.")
        return normalized

    def validate_meter_type(self, meter_type: str | None) -> str | None:
        """Validate optional meter type information when provided."""
        if meter_type is None:
            return None
        if not isinstance(meter_type, str):
            raise ValidationException("Meter type must be a string.")
        normalized = meter_type.strip()
        if not normalized:
            return None
        if len(normalized) > 50:
            raise ValidationException("Meter type is too long.")
        return normalized

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Validate that the electricity amount is positive and within the supported range."""
        if amount is None:
            raise ValidationException("Amount is required.")
        amount_value = Decimal(str(amount))
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")
        if amount_value < Decimal("100"):
            raise ValidationException("Amount must be at least 100.")
        if amount_value > Decimal("5000000"):
            raise ValidationException("Amount must not exceed 5000000.")
        return amount_value.quantize(Decimal("0.01"))

    async def validate_wallet(self, *, wallet: Wallet | None, wallet_id: UUID | None, user_id: UUID | None) -> Wallet:
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
        if not getattr(wallet_record, "is_active", True):
            raise WalletException("Wallet is not active.")
        if getattr(wallet_record, "is_frozen", False) or getattr(wallet_record, "is_suspended", False):
            raise WalletException("Wallet is frozen or suspended.")
        return wallet_record

    async def validate_transaction_pin(self, *, user_id: UUID | None, transaction_pin: str | None, wallet: Wallet | None) -> None:
        """Validate that a transaction PIN exists, is well-formed, and is correct when a wallet service is available."""
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")

        if self.wallet_service is None or user_id is None:
            return
        is_valid = await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)
        if not is_valid:
            raise AuthenticationException("Transaction PIN is invalid.")

    def normalize_customer_name(self, customer_name: str | None) -> str | None:
        """Normalize an optional customer-name value for downstream consumption."""
        if customer_name is None:
            return None
        if not isinstance(customer_name, str):
            raise ValidationException("Customer name must be a string.")
        normalized = re.sub(r"\s+", " ", customer_name.strip())
        return normalized or None


__all__ = ["ElectricityValidationService"]
