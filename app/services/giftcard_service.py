from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.giftcard.pricing import GiftCardPricingService
from app.services.giftcard.reconciliation import GiftCardReconciliationService
from app.services.giftcard.settlement import GiftCardSettlementService
from app.services.giftcard.trading import GiftCardTradingService
from app.services.giftcard.valuation import GiftCardValuationService


class GiftCardService:
    """Thin public facade for gift card-domain orchestration."""

    def __init__(
        self,
        *,
        trading_service: GiftCardTradingService,
        valuation_service: GiftCardValuationService,
        pricing_service: GiftCardPricingService,
        settlement_service: GiftCardSettlementService,
        reconciliation_service: GiftCardReconciliationService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.trading_service = trading_service
        self.valuation_service = valuation_service
        self.pricing_service = pricing_service
        self.settlement_service = settlement_service
        self.reconciliation_service = reconciliation_service
        self.logger = logger or logging.getLogger(__name__)

    async def buy_giftcard(
        self,
        *,
        user_id: UUID,
        brand: str,
        card_type: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        country: str | None = None,
        currency: str = "NGN",
        wallet_id: UUID | None = None,
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card purchase initiation to the trading service."""
        self._log_entry("buy_giftcard", user_id=user_id, brand=brand, card_type=card_type)
        result = await self.trading_service.buy_giftcard(
            user_id=user_id,
            brand=brand,
            card_type=card_type,
            amount=amount,
            transaction_pin=transaction_pin,
            country=country,
            currency=currency,
            wallet_id=wallet_id,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
            reference=reference,
        )
        self._log_completion("buy_giftcard", reference=result.get("reference"))
        return result

    async def sell_giftcard(
        self,
        *,
        user_id: UUID,
        brand: str,
        card_type: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        country: str | None = None,
        currency: str = "NGN",
        wallet_id: UUID | None = None,
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card sell initiation to the trading service."""
        self._log_entry("sell_giftcard", user_id=user_id, brand=brand, card_type=card_type)
        result = await self.trading_service.sell_giftcard(
            user_id=user_id,
            brand=brand,
            card_type=card_type,
            amount=amount,
            transaction_pin=transaction_pin,
            country=country,
            currency=currency,
            wallet_id=wallet_id,
            description=description,
            provider_name=provider_name,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
            reference=reference,
        )
        self._log_completion("sell_giftcard", reference=result.get("reference"))
        return result

    async def process_giftcard_trade(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card trade execution to the trading service."""
        self._log_entry("process_giftcard_trade", reference=reference)
        result = await self.trading_service.process_giftcard_trade(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("process_giftcard_trade", reference=result.get("reference") or reference)
        return result

    async def retry_giftcard_trade(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card trade retry to the trading service."""
        self._log_entry("retry_giftcard_trade", reference=reference)
        result = await self.trading_service.retry_giftcard_trade(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )
        self._log_completion("retry_giftcard_trade", reference=result.get("reference") or reference)
        return result

    async def get_trade_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate gift card trade status lookup to the trading service."""
        self._log_entry("get_trade_status", reference=reference)
        result = await self.trading_service.get_trade_status(reference=reference)
        self._log_completion("get_trade_status", reference=reference)
        return result

    async def get_trade_details(self, *, reference: str) -> dict[str, Any]:
        """Delegate gift card trade detail lookup to the trading service."""
        self._log_entry("get_trade_details", reference=reference)
        result = await self.trading_service.get_trade_details(reference=reference)
        self._log_completion("get_trade_details", reference=reference)
        return result

    async def calculate_valuation(
        self,
        *,
        amount: Decimal | float | int,
        brand: str,
        card_type: str,
        country: str | None = None,
        currency: str = "NGN",
        provider_name: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card valuation to the valuation service."""
        self._log_entry("calculate_valuation", brand=brand, card_type=card_type)
        result = await self.valuation_service.calculate_valuation(
            amount=amount,
            brand=brand,
            card_type=card_type,
            country=country,
            currency=currency,
            provider_name=provider_name,
            promotion_code=promotion_code,
            provider_operation=provider_operation,
        )
        self._log_completion("calculate_valuation")
        return result

    async def calculate_pricing(
        self,
        *,
        amount: Decimal | float | int,
        brand: str,
        card_type: str,
        country: str | None = None,
        denomination: str | None = None,
        card_format: str | None = None,
        currency: str = "NGN",
        provider_name: str | None = None,
        promotion_code: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card pricing to the pricing service."""
        self._log_entry("calculate_pricing", brand=brand, card_type=card_type)
        result = await self.pricing_service.calculate_pricing(
            amount=amount,
            brand=brand,
            card_type=card_type,
            country=country,
            denomination=denomination,
            card_format=card_format,
            currency=currency,
            provider_name=provider_name,
            promotion_code=promotion_code,
            provider_operation=provider_operation,
        )
        self._log_completion("calculate_pricing")
        return result

    async def settle_giftcard_transaction(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        partial_amount: Decimal | float | int | None = None,
        settlement_reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card settlement to the settlement service."""
        self._log_entry("settle_giftcard_transaction", reference=reference)
        result = await self.settlement_service.settle_giftcard_transaction(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            partial_amount=partial_amount,
            settlement_reference=settlement_reference,
        )
        self._log_completion("settle_giftcard_transaction", reference=result.get("reference") or reference)
        return result

    async def retry_giftcard_settlement(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        partial_amount: Decimal | float | int | None = None,
        settlement_reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card settlement retry to the settlement service."""
        self._log_entry("retry_giftcard_settlement", reference=reference)
        result = await self.settlement_service.retry_giftcard_settlement(
            transaction=transaction,
            reference=reference,
            provider_operation=provider_operation,
            provider_name=provider_name,
            partial_amount=partial_amount,
            settlement_reference=settlement_reference,
        )
        self._log_completion("retry_giftcard_settlement", reference=result.get("reference") or reference)
        return result

    async def get_settlement_status(self, *, reference: str) -> dict[str, Any]:
        """Delegate settlement status lookup to the settlement service."""
        self._log_entry("get_settlement_status", reference=reference)
        result = await self.settlement_service.get_settlement_status(reference=reference)
        self._log_completion("get_settlement_status", reference=reference)
        return result

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Delegate gift card transaction reconciliation to the reconciliation service."""
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
        """Delegate pending gift card reconciliation to the reconciliation service."""
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
        """Delegate failed gift card reconciliation to the reconciliation service."""
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
        """Delegate reconciliation report generation to the reconciliation service."""
        self._log_entry("generate_reconciliation_report")
        result = await self.reconciliation_service.generate_reconciliation_report(page_size=page_size)
        self._log_completion("generate_reconciliation_report")
        return result

    async def get_transaction_user_id_by_reference(self, reference: str) -> UUID | None:
        """Get the user_id for a transaction by reference. Used for authorization checks."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            return None
        return transaction.user_id

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("giftcard_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("giftcard_service_completion", extra={"operation": operation, **context})


__all__ = ["GiftCardService"]
