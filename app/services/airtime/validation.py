from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.wallet import Wallet
from app.services.wallet_service import WalletService
from app.utils.exceptions import AuthenticationException, ValidationException, WalletException


class AirtimeValidationService:
    """Validate airtime purchase inputs without performing provider or database writes."""

    def __init__(self, *, wallet_service: WalletService | None = None) -> None:
        self.wallet_service = wallet_service

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal | float | int,
        network: str | None = None,
        transaction_pin: str | None = None,
        wallet: Wallet | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Validate the core airtime purchase request and return normalized values."""
        normalized_phone = self.normalize_phone_number(phone_number)
        network_name = self.validate_network(network or self._infer_network(normalized_phone))
        amount_value = self.validate_amount(amount)
        wallet_record = await self.validate_wallet(wallet=wallet, wallet_id=wallet_id, user_id=user_id)
        await self.validate_transaction_pin(user_id=user_id, transaction_pin=transaction_pin, wallet=wallet_record)

        return {
            "phone_number": normalized_phone,
            "network": network_name,
            "amount": amount_value,
            "wallet": wallet_record,
        }

    def validate_network(self, network: str | None) -> str:
        """Validate the supplied network code or name against supported operators."""
        if not network or not isinstance(network, str) or not network.strip():
            raise ValidationException("Network is required.")
        normalized = network.strip().upper()
        supported = {"MTN", "AIRTEL", "GLO", "9MOBILE"}
        if normalized not in supported:
            raise ValidationException("Unsupported network operator.")
        return normalized

    def validate_phone_number(self, phone_number: str | None) -> str:
        """Validate and normalize a Nigerian or international phone number."""
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise ValidationException("Phone number is required.")
        normalized = self.normalize_phone_number(phone_number)
        if not re.fullmatch(r"\+?234\d{10}", normalized):
            raise ValidationException("Phone number must be a valid Nigerian number.")
        return normalized

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Validate that the airtime amount is positive and within configured limits."""
        if amount is None:
            raise ValidationException("Amount is required.")
        amount_value = Decimal(str(amount))
        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")
        min_amount = Decimal("50")
        max_amount = Decimal("50000")
        if amount_value < min_amount:
            raise ValidationException("Amount must be at least 50.")
        if amount_value > max_amount:
            raise ValidationException("Amount must not exceed 50000.")
        return amount_value.quantize(Decimal("0.01"))

    async def validate_wallet(self, *, wallet: Wallet | None, wallet_id: UUID | None, user_id: UUID | None) -> Wallet:
        """Validate wallet existence, activity, and frozen/suspended state."""
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
        """Validate that a transaction PIN exists, is well-formed, and is correct when a wallet is provided."""
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")

        if self.wallet_service is None:
            return
        if user_id is None:
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

    def _infer_network(self, phone_number: str) -> str | None:
        if not phone_number:
            return None
        digits = re.sub(r"\D", "", phone_number)
        if digits.startswith("234"):
            digits = digits[3:]
        if digits.startswith("0"):
            digits = digits[1:]
        if digits.startswith("70") or digits.startswith("71") or digits.startswith("72") or digits.startswith("73") or digits.startswith("74") or digits.startswith("76"):
            return "MTN"
        if digits.startswith("80") or digits.startswith("81") or digits.startswith("82"):
            return "AIRTEL"
        if digits.startswith("90") or digits.startswith("91"):
            return "MTN"
        if digits.startswith("81"):
            return "GLO"
        if digits.startswith("89"):
            return "GLO"
        if digits.startswith("79"):
            return "9MOBILE"
        return None


__all__ = ["AirtimeValidationService"]
