from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.tv.packages import TVPackageService
from app.services.tv.pricing import TVPricingService
from app.services.tv.purchase import TVPurchaseService
from app.services.tv.reconciliation import TVReconciliationService
from app.services.tv.validation import TVValidationService


class TVService:
    """Thin public facade for TV-domain orchestration."""

    def __init__(
        self,
        *,
        purchase_service: TVPurchaseService,
        validation_service: TVValidationService,
        package_service: TVPackageService,
        pricing_service: TVPricingService,
        reconciliation_service: TVReconciliationService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.purchase_service = purchase_service
        self.validation_service = validation_service
        self.package_service = package_service
        self.pricing_service = pricing_service
        self.reconciliation_service = reconciliation_service
        self.logger = logger or logging.getLogger(__name__)

    async def purchase_tv(
        self,
        *,
        user_id: UUID,
        provider: str,
        smart_card_number: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        package_code: str | None = None,
        service_type: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate TV purchase initiation to the purchase service."""
        self._log_entry("purchase_tv", user_id=user_id, provider=provider)
        result = await self.purchase_service.purchase_tv(
            user_id=user_id,
            provider=provider,
            smart_card_number=smart_card_number,
            amount=amount,
            transaction_pin=transaction_pin,
            package_code=package_code,
            service_type=service_type,
            wallet_id=wallet_id,
            currency=currency,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )
        self._log_completion("purchase_tv", reference=result.get("reference"))
        return result

    async def process_tv_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate TV purchase processing to the purchase service."""
        self._log_entry("process_tv_purchase", reference=reference)
        result = await self.purchase_service.process_tv_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("process_tv_purchase", reference=result.get("reference") or reference)
        return result

    async def retry_tv_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate TV purchase retry to the purchase service."""
        self._log_entry("retry_tv_purchase", reference=reference)
        result = await self.purchase_service.retry_tv_purchase(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("retry_tv_purchase", reference=result.get("reference") or reference)
        return result

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate TV purchase status lookup to the purchase service."""
        self._log_entry("get_purchase_status", reference=reference)
        result = await self.purchase_service.get_purchase_status(reference=reference)
        self._log_completion("get_purchase_status", reference=reference)
        return result

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Delegate TV purchase detail lookup to the purchase service."""
        self._log_entry("get_purchase_details", reference=reference)
        result = await self.purchase_service.get_purchase_details(reference=reference)
        self._log_completion("get_purchase_details", reference=reference)
        return result

    async def validate_purchase_request(
        self,
        *,
        user_id: UUID,
        provider: str,
        smart_card_number: str,
        amount: Decimal | float | int,
        package_code: str | None = None,
        service_type: str | None = None,
        transaction_pin: str | None = None,
        wallet: Any | None = None,
        wallet_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Delegate TV purchase validation to the validation service."""
        self._log_entry("validate_purchase_request", user_id=user_id, provider=provider)
        result = await self.validation_service.validate_purchase_request(
            user_id=user_id,
            provider=provider,
            smart_card_number=smart_card_number,
            amount=amount,
            package_code=package_code,
            service_type=service_type,
            transaction_pin=transaction_pin,
            wallet=wallet,
            wallet_id=wallet_id,
        )
        self._log_completion("validate_purchase_request", user_id=user_id)
        return result

    async def validate_provider(self, provider: str | None) -> Any:
        """Delegate provider validation to the validation service."""
        self._log_entry("validate_provider", provider=provider)
        result = await self.validation_service.validate_provider(provider)
        self._log_completion("validate_provider", provider=provider)
        return result

    def validate_smart_card_number(self, smart_card_number: str | None) -> str:
        """Delegate smart card validation to the validation service."""
        self._log_entry("validate_smart_card_number")
        result = self.validation_service.validate_smart_card_number(smart_card_number)
        self._log_completion("validate_smart_card_number")
        return result

    def validate_package_code(self, package_code: str | None) -> str | None:
        """Delegate TV package code validation to the validation service."""
        self._log_entry("validate_package_code")
        result = self.validation_service.validate_package_code(package_code)
        self._log_completion("validate_package_code")
        return result

    def validate_service_type(self, service_type: str | None) -> str | None:
        """Delegate service type validation to the validation service."""
        self._log_entry("validate_service_type")
        result = self.validation_service.validate_service_type(service_type)
        self._log_completion("validate_service_type")
        return result

    def validate_amount(self, amount: Decimal | float | int | None) -> Decimal:
        """Delegate amount validation to the validation service."""
        self._log_entry("validate_amount")
        result = self.validation_service.validate_amount(amount)
        self._log_completion("validate_amount")
        return result

    async def validate_wallet(self, *, wallet: Any | None, wallet_id: UUID | None, user_id: UUID | None) -> Any:
        """Delegate wallet eligibility validation to the validation service."""
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

    async def get_tv_packages(
        self,
        *,
        provider_name: str | None = None,
        force_refresh: bool = False,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate TV package retrieval to the package service."""
        self._log_entry("get_tv_packages", provider_name=provider_name)
        result = await self.package_service.get_tv_packages(
            provider_name=provider_name,
            force_refresh=force_refresh,
            provider_operation=provider_operation,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        self._log_completion("get_tv_packages", provider_name=provider_name)
        return result

    async def refresh_tv_packages(
        self,
        *,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate TV package refresh to the package service."""
        self._log_entry("refresh_tv_packages", provider_name=provider_name)
        result = await self.package_service.refresh_tv_packages(
            provider_name=provider_name,
            provider_operation=provider_operation,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        self._log_completion("refresh_tv_packages", provider_name=provider_name)
        return result

    async def get_package_by_id(
        self,
        *,
        package_id: str,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate TV package lookup to the package service."""
        self._log_entry("get_package_by_id", package_id=package_id)
        result = await self.package_service.get_package_by_id(
            package_id=package_id,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        self._log_completion("get_package_by_id", package_id=package_id)
        return result

    async def search_packages(
        self,
        *,
        query: str,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate TV package search to the package service."""
        self._log_entry("search_packages", query=query)
        result = await self.package_service.search_packages(
            query=query,
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        self._log_completion("search_packages", query=query)
        return result

    async def filter_packages_by_provider(
        self,
        *,
        provider_name: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate package filtering to the package service."""
        self._log_entry("filter_packages_by_provider", provider_name=provider_name)
        result = await self.package_service.filter_packages_by_provider(
            provider_name=provider_name,
            provider_operation=provider_operation,
        )
        self._log_completion("filter_packages_by_provider", provider_name=provider_name)
        return result

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        provider_name: str | None = None,
        service_type: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate TV pricing calculation to the pricing service."""
        self._log_entry("calculate_pricing", provider_name=provider_name)
        result = await self.pricing_service.calculate_pricing(
            amount=amount,
            provider_name=provider_name,
            service_type=service_type,
            promotion_code=promotion_code,
            provider_operation=provider_operation,
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
        """Delegate TV reconciliation to the reconciliation service."""
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
        """Delegate pending TV reconciliation to the reconciliation service."""
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
        """Delegate failed TV reconciliation to the reconciliation service."""
        self._log_entry("reconcile_failed_transactions")
        result = await self.reconciliation_service.reconcile_failed_transactions(
            provider_operation=provider_operation,
            provider_name=provider_name,
            page_size=page_size,
        )
        self._log_completion("reconcile_failed_transactions")
        return result

    async def retry_reconciliation(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate reconciliation retry to the reconciliation service."""
        self._log_entry("retry_reconciliation", reference=reference)
        result = await self.reconciliation_service.retry_transaction(
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
        )
        self._log_completion("retry_reconciliation", reference=reference)
        return result

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
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
        """Delegate reconciliation reporting to the reconciliation service."""
        self._log_entry("generate_reconciliation_report")
        result = await self.reconciliation_service.generate_reconciliation_report(page_size=page_size)
        self._log_completion("generate_reconciliation_report")
        return result

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("tv_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("tv_service_completion", extra={"operation": operation, **context})


__all__ = ["TVService"]
