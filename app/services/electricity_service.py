from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.electricity.meter import ElectricityMeterService
from app.services.electricity.pricing import ElectricityPricingService
from app.services.electricity.purchase import ElectricityPurchaseService
from app.services.electricity.reconciliation import ElectricityReconciliationService
from app.services.electricity.validation import ElectricityValidationService


class ElectricityService:
    """Thin public facade for electricity-domain orchestration."""

    def __init__(
        self,
        *,
        purchase_service: ElectricityPurchaseService | None = None,
        validation_service: ElectricityValidationService | None = None,
        meter_service: ElectricityMeterService | None = None,
        pricing_service: ElectricityPricingService | None = None,
        reconciliation_service: ElectricityReconciliationService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.purchase_service = purchase_service
        self.validation_service = validation_service
        self.meter_service = meter_service
        self.pricing_service = pricing_service
        self.reconciliation_service = reconciliation_service
        self.logger = logger or logging.getLogger(__name__)

    def _require_purchase_service(self) -> ElectricityPurchaseService:
        if self.purchase_service is None:
            raise RuntimeError("Electricity purchase service is required.")
        return self.purchase_service

    def _require_validation_service(self) -> ElectricityValidationService:
        if self.validation_service is None:
            raise RuntimeError("Electricity validation service is required.")
        return self.validation_service

    def _require_meter_service(self) -> ElectricityMeterService:
        if self.meter_service is None:
            raise RuntimeError("Electricity meter service is required.")
        return self.meter_service

    def _require_pricing_service(self) -> ElectricityPricingService:
        if self.pricing_service is None:
            raise RuntimeError("Electricity pricing service is required.")
        return self.pricing_service

    def _require_reconciliation_service(self) -> ElectricityReconciliationService:
        if self.reconciliation_service is None:
            raise RuntimeError("Electricity reconciliation service is required.")
        return self.reconciliation_service

    async def purchase_electricity(
        self,
        *,
        user_id: UUID,
        meter_number: str,
        disco: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        meter_type: str | None = None,
        customer_name: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate electricity purchase initiation to the purchase service."""
        self._log_entry("purchase_electricity", user_id=user_id, meter_number=meter_number, disco=disco)
        service = self._require_purchase_service()
        result = await service.purchase_electricity(
            user_id=user_id,
            meter_number=meter_number,
            disco=disco,
            amount=amount,
            transaction_pin=transaction_pin,
            meter_type=meter_type,
            customer_name=customer_name,
            wallet_id=wallet_id,
            currency=currency,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )
        self._log_completion("purchase_electricity", reference=result.get("reference"))
        return result

    async def process_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate electricity purchase processing to the purchase service."""
        self._log_entry("process_electricity_purchase", reference=reference)
        service = self._require_purchase_service()
        result = await service.process_electricity_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("process_electricity_purchase", reference=result.get("reference"))
        return result

    async def reverse_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Delegate electricity purchase reversal to the purchase service."""
        self._log_entry("reverse_electricity_purchase", reference=reference)
        service = self._require_purchase_service()
        result = await service.reverse_electricity_purchase(transaction=transaction, reference=reference, reason=reason)
        self._log_completion("reverse_electricity_purchase", reference=result.get("reference"))
        return result

    async def retry_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate electricity purchase retry to the purchase service."""
        self._log_entry("retry_electricity_purchase", reference=reference)
        service = self._require_purchase_service()
        result = await service.retry_electricity_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("retry_electricity_purchase", reference=result.get("reference"))
        return result

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate purchase status retrieval to the purchase service."""
        self._log_entry("get_purchase_status", reference=reference)
        service = self._require_purchase_service()
        result = await service.get_purchase_status(reference=reference)
        self._log_completion("get_purchase_status", reference=reference)
        return result

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Delegate purchase detail retrieval to the purchase service."""
        self._log_entry("get_purchase_details", reference=reference)
        service = self._require_purchase_service()
        result = await service.get_purchase_details(reference=reference)
        self._log_completion("get_purchase_details", reference=reference)
        return result

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
        wallet: Any | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Delegate purchase request validation to the validation service."""
        self._log_entry("validate_purchase_request", user_id=user_id)
        service = self._require_validation_service()
        result = await service.validate_purchase_request(
            user_id=user_id,
            meter_number=meter_number,
            disco=disco,
            amount=amount,
            transaction_pin=transaction_pin,
            meter_type=meter_type,
            customer_name=customer_name,
            wallet=wallet,
            wallet_id=wallet_id,
        )
        self._log_completion("validate_purchase_request", user_id=user_id)
        return result

    async def verify_meter(
        self,
        *,
        meter_number: str,
        disco: str,
        meter_type: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate meter verification to the meter service."""
        self._log_entry("verify_meter", meter_number=meter_number, disco=disco)
        service = self._require_meter_service()
        result = await service.verify_meter(
            meter_number=meter_number,
            disco=disco,
            meter_type=meter_type,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        self._log_completion("verify_meter", reference=result.get("reference"))
        return result

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        provider_name: str | None = None,
        promotion_code: str | None = None,
        disco: str | None = None,
        meter_type: str | None = None,
    ) -> dict[str, Any]:
        """Delegate electricity pricing calculation to the pricing service."""
        self._log_entry("calculate_pricing", provider_name=provider_name, disco=disco)
        service = self._require_pricing_service()
        result = await service.calculate_pricing(
            amount=amount,
            provider_name=provider_name,
            promotion_code=promotion_code,
            disco=disco,
            meter_type=meter_type,
        )
        self._log_completion("calculate_pricing", provider_name=provider_name)
        return result

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate single transaction reconciliation to the reconciliation service."""
        self._log_entry("reconcile_transaction", reference=reference)
        service = self._require_reconciliation_service()
        result = await service.reconcile_transaction(
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
        """Delegate pending transaction reconciliation to the reconciliation service."""
        self._log_entry("reconcile_pending_transactions")
        service = self._require_reconciliation_service()
        result = await service.reconcile_pending_transactions(
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
        """Delegate failed transaction reconciliation to the reconciliation service."""
        self._log_entry("reconcile_failed_transactions")
        service = self._require_reconciliation_service()
        result = await service.reconcile_failed_transactions(
            provider_operation=provider_operation,
            provider_name=provider_name,
            page_size=page_size,
        )
        self._log_completion("reconcile_failed_transactions")
        return result

    async def generate_reconciliation_report(self, *, page_size: int | None = None) -> dict[str, Any]:
        """Delegate reconciliation report generation to the reconciliation service."""
        self._log_entry("generate_reconciliation_report")
        service = self._require_reconciliation_service()
        result = await service.generate_reconciliation_report(page_size=page_size)
        self._log_completion("generate_reconciliation_report")
        return result

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("electricity_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("electricity_service_completion", extra={"operation": operation, **context})


__all__ = ["ElectricityService"]
