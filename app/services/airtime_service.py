from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.airtime.purchase import AirtimePurchaseService
from app.services.airtime.reconciliation import AirtimeReconciliationService
from app.services.airtime.validation import AirtimeValidationService


class AirtimePricingService(Protocol):
    """Protocol for optional airtime pricing implementations."""

    async def calculate_price(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal: ...

    async def calculate_discount(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal: ...

    async def calculate_cashback(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal: ...

    async def calculate_commission(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal: ...

    async def calculate_final_amount(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal: ...


class AirtimeService:
    """Thin public facade for airtime-domain orchestration."""

    def __init__(
        self,
        *,
        purchase_service: AirtimePurchaseService,
        validation_service: AirtimeValidationService,
        pricing_service: AirtimePricingService | None = None,
        reconciliation_service: AirtimeReconciliationService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.purchase_service = purchase_service
        self.validation_service = validation_service
        self.pricing_service = pricing_service
        self.reconciliation_service = reconciliation_service
        self.logger = logger or logging.getLogger(__name__)

    async def purchase_airtime(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Any | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate airtime purchase initiation to the purchase service."""
        self._log_entry("purchase_airtime", user_id=user_id, phone_number=phone_number)
        result = await self.purchase_service.purchase_airtime(
            user_id=user_id,
            phone_number=phone_number,
            amount=amount,
            transaction_pin=transaction_pin,
            wallet_id=wallet_id,
            currency=currency,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )
        self._log_completion("purchase_airtime", reference=result.get("reference"))
        return result

    async def process_airtime_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate airtime purchase processing to the purchase service."""
        self._log_entry("process_airtime_purchase", reference=reference)
        result = await self.purchase_service.process_airtime_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("process_airtime_purchase", reference=reference)
        return result

    async def reverse_airtime_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Delegate airtime purchase reversal to the purchase service."""
        self._log_entry("reverse_airtime_purchase", reference=reference)
        result = await self.purchase_service.reverse_airtime_purchase(transaction=transaction, reference=reference, reason=reason)
        self._log_completion("reverse_airtime_purchase", reference=reference)
        return result

    async def retry_airtime_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate airtime purchase retry to the purchase service."""
        self._log_entry("retry_airtime_purchase", reference=reference)
        result = await self.purchase_service.retry_airtime_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("retry_airtime_purchase", reference=reference)
        return result

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate airtime purchase status lookup to the purchase service."""
        self._log_entry("get_purchase_status", reference=reference)
        result = await self.purchase_service.get_purchase_status(reference=reference)
        self._log_completion("get_purchase_status", reference=reference)
        return result

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Delegate airtime purchase detail lookup to the purchase service."""
        self._log_entry("get_purchase_details", reference=reference)
        result = await self.purchase_service.get_purchase_details(reference=reference)
        self._log_completion("get_purchase_details", reference=reference)
        return result

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal | float | int,
        network: str | None = None,
        transaction_pin: str | None = None,
        wallet: Any | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Delegate airtime purchase validation to the validation service."""
        self._log_entry("validate_purchase_request", user_id=user_id)
        result = await self.validation_service.validate_purchase_request(
            user_id=user_id,
            phone_number=phone_number,
            amount=amount,
            network=network,
            transaction_pin=transaction_pin,
            wallet=wallet,
            wallet_id=wallet_id,
        )
        self._log_completion("validate_purchase_request", user_id=user_id)
        return result

    def validate_network(self, network: str | None) -> str:
        """Delegate network validation to the validation service."""
        self._log_entry("validate_network")
        result = self.validation_service.validate_network(network)
        self._log_completion("validate_network")
        return result

    def validate_phone_number(self, phone_number: str | None) -> str:
        """Delegate phone-number validation to the validation service."""
        self._log_entry("validate_phone_number")
        result = self.validation_service.validate_phone_number(phone_number)
        self._log_completion("validate_phone_number")
        return result

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Delegate amount validation to the validation service."""
        self._log_entry("validate_amount")
        result = self.validation_service.validate_amount(amount)
        self._log_completion("validate_amount")
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

    async def calculate_price(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal:
        """Delegate price calculation to the pricing service when available."""
        self._log_entry("calculate_price")
        if self.pricing_service is None:
            return Decimal(str(amount))
        result = await self.pricing_service.calculate_price(amount=amount, network=network)
        self._log_completion("calculate_price")
        return result

    async def calculate_discount(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal:
        """Delegate discount calculation to the pricing service when available."""
        self._log_entry("calculate_discount")
        if self.pricing_service is None:
            return Decimal("0")
        result = await self.pricing_service.calculate_discount(amount=amount, network=network)
        self._log_completion("calculate_discount")
        return result

    async def calculate_cashback(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal:
        """Delegate cashback calculation to the pricing service when available."""
        self._log_entry("calculate_cashback")
        if self.pricing_service is None:
            return Decimal("0")
        result = await self.pricing_service.calculate_cashback(amount=amount, network=network)
        self._log_completion("calculate_cashback")
        return result

    async def calculate_commission(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal:
        """Delegate commission calculation to the pricing service when available."""
        self._log_entry("calculate_commission")
        if self.pricing_service is None:
            return Decimal("0")
        result = await self.pricing_service.calculate_commission(amount=amount, network=network)
        self._log_completion("calculate_commission")
        return result

    async def calculate_final_amount(self, *, amount: Decimal | float | int, network: str | None = None) -> Decimal:
        """Delegate final amount calculation to the pricing service when available."""
        self._log_entry("calculate_final_amount")
        if self.pricing_service is None:
            return Decimal(str(amount))
        result = await self.pricing_service.calculate_final_amount(amount=amount, network=network)
        self._log_completion("calculate_final_amount")
        return result

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate transaction reconciliation to the reconciliation service."""
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
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate pending transaction reconciliation to the reconciliation service."""
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
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate failed transaction reconciliation to the reconciliation service."""
        self._log_entry("reconcile_failed_transactions")
        result = await self.reconciliation_service.reconcile_failed_transactions(
            provider_operation=provider_operation,
            provider_name=provider_name,
            page_size=page_size,
        )
        self._log_completion("reconcile_failed_transactions")
        return result

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Delegate discrepancy detection to the reconciliation service."""
        self._log_entry("detect_discrepancies", reference=transaction.reference)
        result = await self.reconciliation_service.detect_discrepancies(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )
        self._log_completion("detect_discrepancies", reference=transaction.reference)
        return result

    async def generate_reconciliation_report(self, *, page_size: int | None = None) -> dict[str, Any]:
        """Delegate reconciliation report generation to the reconciliation service."""
        self._log_entry("generate_reconciliation_report")
        result = await self.reconciliation_service.generate_reconciliation_report(page_size=page_size)
        self._log_completion("generate_reconciliation_report")
        return result

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("airtime_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("airtime_service_completion", extra={"operation": operation, **context})


__all__ = ["AirtimeService"]
