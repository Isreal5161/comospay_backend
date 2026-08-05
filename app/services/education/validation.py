from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.provider import Provider
from app.models.wallet import Wallet
from app.repositories.provider_repository import ProviderRepository
from app.services.wallet_service import WalletService
from app.utils.exceptions import AuthenticationException, ValidationException, WalletException


EDUCATION_EXAMINATION_ALIASES: dict[str, str] = {
    "WAEC": "WAEC",
    "WAEC SSCE": "WAEC",
    "NECO": "NECO",
    "NABTEB": "NABTEB",
    "JAMB": "JAMB",
    "UTME": "JAMB",
    "REMITA": "REMITA",
}

SUPPORTED_EDUCATION_EXAMINATIONS: set[str] = set(EDUCATION_EXAMINATION_ALIASES.values())


class EducationValidationService:
    """Validate education-domain purchase requests without external provider communication."""

    def __init__(self, *, wallet_service: WalletService | None = None, provider_repository: ProviderRepository) -> None:
        self.wallet_service = wallet_service
        self.provider_repository = provider_repository

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID,
        examination_type: str,
        provider: str,
        candidate_number: str,
        amount: Decimal | float | int,
        quantity: int,
        examination_year: int | str,
        transaction_pin: str | None = None,
        wallet: Wallet | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Validate the core education purchase request and return normalized values."""
        normalized_examination_type = self.validate_examination_type(examination_type)
        provider_record = await self.validate_provider(provider)
        normalized_candidate_number = self.validate_candidate_number(candidate_number, normalized_examination_type)
        normalized_year = self.validate_examination_year(examination_year)
        amount_value = self.validate_amount(amount)
        quantity_value = self.validate_quantity(quantity)
        wallet_record = await self.validate_wallet(wallet=wallet, wallet_id=wallet_id, user_id=user_id)
        await self.validate_transaction_pin(user_id=user_id, transaction_pin=transaction_pin, wallet=wallet_record)

        return {
            "examination_type": normalized_examination_type,
            "provider": provider_record.name,
            "provider_record": provider_record,
            "candidate_number": normalized_candidate_number,
            "amount": amount_value,
            "quantity": quantity_value,
            "examination_year": normalized_year,
            "wallet": wallet_record,
        }

    async def validate_provider(self, provider: str | None) -> Provider:
        """Validate that the education provider is supported and configured in the database."""
        if not provider or not isinstance(provider, str) or not provider.strip():
            raise ValidationException("Education provider is required.")

        normalized = provider.strip()
        providers = await self.provider_repository.get_active_providers(category="Education")
        for candidate in providers:
            candidate_name = candidate.name.strip().upper() if candidate.name else ""
            candidate_code = candidate.code.strip().upper() if getattr(candidate, "code", None) else ""
            if normalized.upper() in {candidate_name, candidate_code}:
                return candidate

        raise ValidationException("Education provider is not configured or is unavailable.")

    def validate_examination_type(self, examination_type: str | None) -> str:
        """Validate and normalize the examination type for education purchases."""
        if not examination_type or not isinstance(examination_type, str) or not examination_type.strip():
            raise ValidationException("Examination type is required.")

        normalized = examination_type.strip().upper()
        normalized = EDUCATION_EXAMINATION_ALIASES.get(normalized, normalized)
        if normalized not in SUPPORTED_EDUCATION_EXAMINATIONS:
            raise ValidationException("Unsupported education examination type.")
        return normalized

    def validate_examination_year(self, examination_year: int | str | None) -> int:
        """Validate the examination year against business rules."""
        if examination_year is None:
            raise ValidationException("Examination year is required.")

        try:
            year_value = int(examination_year)
        except (TypeError, ValueError) as exc:
            raise ValidationException("Examination year must be a valid integer.") from exc

        current_year = datetime.now(timezone.utc).year
        if year_value < 2000 or year_value > current_year + 1:
            raise ValidationException(f"Examination year must be between 2000 and {current_year + 1}.")
        return year_value

    def validate_candidate_number(self, candidate_number: str | None, examination_type: str) -> str:
        """Validate the candidate or registration number format."""
        if not candidate_number or not isinstance(candidate_number, str) or not candidate_number.strip():
            raise ValidationException("Candidate or registration number is required.")

        normalized = re.sub(r"[\s-]+", "", candidate_number.strip()).upper()
        if len(normalized) < 4 or len(normalized) > 50:
            raise ValidationException("Candidate or registration number must be between 4 and 50 characters.")

        if examination_type == "JAMB":
            if not re.fullmatch(r"\d{10}", normalized):
                raise ValidationException("JAMB registration number must be exactly 10 digits.")
            return normalized

        if not re.fullmatch(r"[A-Z0-9/]+", normalized):
            raise ValidationException("Candidate or registration number contains invalid characters.")
        return normalized

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Validate that the education purchase amount is positive and within expected business limits."""
        if amount is None:
            raise ValidationException("Amount is required.")

        try:
            amount_value = Decimal(str(amount))
        except (TypeError, ValueError) as exc:
            raise ValidationException("Amount must be a valid numeric value.") from exc

        if amount_value <= 0:
            raise ValidationException("Amount must be greater than zero.")
        if amount_value < Decimal("100"):
            raise ValidationException("Amount must be at least 100.")
        if amount_value > Decimal("5000000"):
            raise ValidationException("Amount must not exceed 5000000.")
        return amount_value.quantize(Decimal("0.01"))

    def validate_quantity(self, quantity: int | None) -> int:
        """Validate the purchase quantity for education PINs or vouchers."""
        if quantity is None:
            raise ValidationException("Quantity is required.")
        if not isinstance(quantity, int):
            raise ValidationException("Quantity must be an integer.")
        if quantity < 1:
            raise ValidationException("Quantity must be at least 1.")
        if quantity > 100:
            raise ValidationException("Quantity must not exceed 100.")
        return quantity

    async def validate_wallet(self, *, wallet: Wallet | None, wallet_id: UUID | None, user_id: UUID | None) -> Wallet:
        """Validate wallet eligibility and state for education purchases."""
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
        """Validate the transaction PIN when a wallet service is available."""
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin.strip()):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")

        if self.wallet_service is None or user_id is None:
            return

        is_valid = await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin.strip())
        if not is_valid:
            raise AuthenticationException("Transaction PIN is invalid.")


__all__ = ["EducationValidationService"]
