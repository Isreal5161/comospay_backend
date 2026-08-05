from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.education.pricing import EducationPricingService
from app.services.education.purchase import EducationPurchaseService
from app.services.education.reconciliation import EducationReconciliationService
from app.services.education.result import EducationResultService
from app.services.education.validation import EducationValidationService


class EducationService:
    """Thin public facade for education-domain orchestration."""

    def __init__(
        self,
        *,
        purchase_service: EducationPurchaseService | None = None,
        validation_service: EducationValidationService | None = None,
        pricing_service: EducationPricingService | None = None,
        result_service: EducationResultService | None = None,
        reconciliation_service: EducationReconciliationService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.purchase_service = purchase_service
        self.validation_service = validation_service
        self.pricing_service = pricing_service
        self.result_service = result_service
        self.reconciliation_service = reconciliation_service
        self.logger = logger or logging.getLogger(__name__)

    async def purchase_education(
        self,
        *,
        user_id: UUID,
        examination_type: str,
        provider: str,
        candidate_number: str,
        amount: Decimal | float | int,
        quantity: int,
        examination_year: int | str,
        transaction_pin: str,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate education purchase initiation to the purchase service."""
        self._log_entry("purchase_education", user_id=user_id, provider=provider)
        result = await self.purchase_service.purchase_education(
            user_id=user_id,
            examination_type=examination_type,
            provider=provider,
            candidate_number=candidate_number,
            amount=amount,
            quantity=quantity,
            examination_year=examination_year,
            transaction_pin=transaction_pin,
            wallet_id=wallet_id,
            currency=currency,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )
        self._log_completion("purchase_education", reference=result.get("reference"))
        return result

    async def process_education_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate education purchase processing to the purchase service."""
        self._log_entry("process_education_purchase", reference=reference)
        result = await self.purchase_service.process_education_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("process_education_purchase", reference=result.get("reference") or reference)
        return result

    async def retry_education_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate education purchase retry to the purchase service."""
        self._log_entry("retry_education_purchase", reference=reference)
        result = await self.purchase_service.retry_education_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("retry_education_purchase", reference=result.get("reference") or reference)
        return result

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate education purchase status lookup to the purchase service."""
        self._log_entry("get_purchase_status", reference=reference)
        result = await self.purchase_service.get_purchase_status(reference=reference)
        self._log_completion("get_purchase_status", reference=reference)
        return result

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Delegate education purchase detail lookup to the purchase service."""
        self._log_entry("get_purchase_details", reference=reference)
        result = await self.purchase_service.get_purchase_details(reference=reference)
        self._log_completion("get_purchase_details", reference=reference)
        return result

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
        wallet: Any | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Delegate education purchase validation to the validation service."""
        self._log_entry("validate_purchase_request", user_id=user_id)
        result = await self.validation_service.validate_purchase_request(
            user_id=user_id,
            examination_type=examination_type,
            provider=provider,
            candidate_number=candidate_number,
            amount=amount,
            quantity=quantity,
            examination_year=examination_year,
            transaction_pin=transaction_pin,
            wallet=wallet,
            wallet_id=wallet_id,
        )
        self._log_completion("validate_purchase_request", user_id=user_id)
        return result

    async def validate_provider(self, provider: str | None) -> Any:
        """Delegate education provider validation to the validation service."""
        self._log_entry("validate_provider", provider=provider)
        result = await self.validation_service.validate_provider(provider)
        self._log_completion("validate_provider", provider=provider)
        return result

    def validate_examination_type(self, examination_type: str | None) -> str:
        """Delegate examination type validation to the validation service."""
        self._log_entry("validate_examination_type")
        result = self.validation_service.validate_examination_type(examination_type)
        self._log_completion("validate_examination_type")
        return result

    def validate_candidate_number(self, candidate_number: str | None, examination_type: str) -> str:
        """Delegate candidate number validation to the validation service."""
        self._log_entry("validate_candidate_number")
        result = self.validation_service.validate_candidate_number(candidate_number, examination_type)
        self._log_completion("validate_candidate_number")
        return result

    def validate_examination_year(self, examination_year: int | str | None) -> int:
        """Delegate examination year validation to the validation service."""
        self._log_entry("validate_examination_year")
        result = self.validation_service.validate_examination_year(examination_year)
        self._log_completion("validate_examination_year")
        return result

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Delegate amount validation to the validation service."""
        self._log_entry("validate_amount")
        result = self.validation_service.validate_amount(amount)
        self._log_completion("validate_amount")
        return result

    def validate_quantity(self, quantity: int | None) -> int:
        """Delegate quantity validation to the validation service."""
        self._log_entry("validate_quantity")
        result = self.validation_service.validate_quantity(quantity)
        self._log_completion("validate_quantity")
        return result

    async def validate_wallet(self, *, wallet: Any | None, wallet_id: UUID | None, user_id: UUID | None) -> Any:
        """Delegate wallet validation to the validation service."""
        self._log_entry("validate_wallet", user_id=user_id)
        result = await self.validation_service.validate_wallet(wallet=wallet, wallet_id=wallet_id, user_id=user_id)
        self._log_completion("validate_wallet", user_id=user_id)
        return result

    async def validate_transaction_pin(self, *, user_id: UUID | None, transaction_pin: str | None, wallet: Any | None) -> None:
        """Delegate transaction PIN validation to the validation service."""
        self._log_entry("validate_transaction_pin", user_id=user_id)
        result = await self.validation_service.validate_transaction_pin(user_id=user_id, transaction_pin=transaction_pin, wallet=wallet)
        self._log_completion("validate_transaction_pin", user_id=user_id)
        return result

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        provider_name: str | None = None,
        examination_type: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate education pricing calculation to the pricing service."""
        self._log_entry("calculate_pricing", provider_name=provider_name, examination_type=examination_type)
        result = await self.pricing_service.calculate_pricing(
            amount=amount,
            provider_name=provider_name,
            examination_type=examination_type,
            promotion_code=promotion_code,
            provider_operation=provider_operation,
        )
        self._log_completion("calculate_pricing", provider_name=provider_name, examination_type=examination_type)
        return result

    async def process_result(
        self,
        *,
        provider_response: Any,
        provider_name: str | None = None,
        examination_type: str | None = None,
        transaction_reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate education provider response normalization to the result service."""
        self._log_entry("process_result", provider_name=provider_name)
        result = await self.result_service.process_result(
            provider_response=provider_response,
            provider_name=provider_name,
            examination_type=examination_type,
            transaction_reference=transaction_reference,
        )
        self._log_completion("process_result", transaction_reference=result.get("transaction_reference"))
        return result

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate single education transaction reconciliation to the reconciliation service."""
        self._log_entry("reconcile_transaction", reference=reference)
        result = await self.reconciliation_service.reconcile_transaction(
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )
        self._log_completion("reconcile_transaction", reference=reference)
        return result

    async def reconcile_pending_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate pending education reconciliation to the reconciliation service."""
        self._log_entry("reconcile_pending_transactions")
        result = await self.reconciliation_service.reconcile_pending_transactions(
            provider_operation=provider_operation,
            provider_name=provider_name,
            page_size=page_size,
        )
        self._log_completion("reconcile_pending_transactions")
        return result

    async def reconcile_failed_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate failed education reconciliation to the reconciliation service."""
        self._log_entry("reconcile_failed_transactions")
        result = await self.reconciliation_service.reconcile_failed_transactions(
            provider_operation=provider_operation,
            provider_name=provider_name,
            page_size=page_size,
        )
        self._log_completion("reconcile_failed_transactions")
        return result

    async def retry_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate education reconciliation retry to the reconciliation service."""
        self._log_entry("retry_transaction", reference=reference)
        result = await self.reconciliation_service.retry_transaction(
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )
        self._log_completion("retry_transaction", reference=reference)
        return result

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Delegate education discrepancy detection to the reconciliation service."""
        self._log_entry("detect_discrepancies", transaction_reference=transaction.reference)
        result = await self.reconciliation_service.detect_discrepancies(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )
        self._log_completion("detect_discrepancies", transaction_reference=transaction.reference)
        return result

    async def generate_reconciliation_report(self, *, page_size: int | None = None) -> dict[str, Any]:
        """Delegate reconciliation report generation to the reconciliation service."""
        self._log_entry("generate_reconciliation_report")
        result = await self.reconciliation_service.generate_reconciliation_report(page_size=page_size)
        self._log_completion("generate_reconciliation_report")
        return result

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("education_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("education_service_completion", extra={"operation": operation, **context})


__all__ = ["EducationService"]
