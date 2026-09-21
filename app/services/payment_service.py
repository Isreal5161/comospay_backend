from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from app.integrations.payments.flutterwave.client import FlutterwaveClient
from app.integrations.payments.flutterwave.payments import FlutterwavePaymentService
from app.models.provider import Provider
from app.models.transaction import Transaction
from app.services.payment.payment import PaymentManager
from app.services.payment.webhook import PaymentWebhookService
from app.utils.exceptions import ValidationException


class PaymentService:
    """Thin public facade for payment-domain orchestration."""

    def __init__(
        self,
        *,
        payment_manager: PaymentManager,
        webhook_service: PaymentWebhookService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.payment_manager = payment_manager
        self.webhook_service = webhook_service
        self.logger = logger or logging.getLogger(__name__)

    async def initialize_payment(
        self,
        *,
        user_id: UUID,
        amount: Decimal,
        reference: str,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        redirect_url: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
        transaction_type: str = "payment",
        category: str = "payment",
    ) -> dict[str, Any]:
        """Initialize a payment through the internal payment manager."""
        self._log_entry("initialize_payment", reference=reference, user_id=user_id)
        operation = provider_operation or self.build_provider_operation(
            operation="initialize",
            payload={
                "reference": reference,
                "amount": amount,
                "currency": currency,
                "wallet_id": wallet_id,
                "user_id": user_id,
                "description": description,
                "redirect_url": redirect_url,
            },
            provider_name=provider_name,
        )
        result = await self.payment_manager.initialize_payment(
            user_id=user_id,
            amount=amount,
            reference=reference,
            wallet_id=wallet_id,
            currency=currency,
            description=description,
            redirect_url=redirect_url,
            provider_name=provider_name,
            provider_operation=operation,
            metadata_payload=metadata_payload,
            transaction_type=transaction_type,
            category=category,
        )
        self._log_completion("initialize_payment", reference=reference)
        return result

    async def verify_payment(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Verify a payment through the internal payment manager."""
        self._log_entry("verify_payment", reference=reference)
        operation = provider_operation or self.build_provider_operation(operation="verify", payload={"reference": reference})
        result = await self.payment_manager.verify_payment(reference=reference, provider_operation=operation)
        self._log_completion("verify_payment", reference=reference)
        return result

    async def process_successful_payment(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Finalize a successful payment through the internal payment manager."""
        self._log_entry("process_successful_payment", reference=reference)
        result = await self.payment_manager.process_successful_payment(transaction=transaction, reference=reference)
        self._log_completion("process_successful_payment", reference=reference)
        return result

    async def process_failed_payment(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Mark a payment as failed through the internal payment manager."""
        self._log_entry("process_failed_payment", reference=reference)
        result = await self.payment_manager.process_failed_payment(transaction=transaction, reference=reference, reason=reason)
        self._log_completion("process_failed_payment", reference=reference)
        return result

    async def cancel_payment(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a payment through the internal payment manager."""
        self._log_entry("cancel_payment", reference=reference)
        result = await self.payment_manager.cancel_payment(transaction=transaction, reference=reference, reason=reason)
        self._log_completion("cancel_payment", reference=reference)
        return result

    async def refund_payment(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Refund a payment through the internal payment manager."""
        self._log_entry("refund_payment", reference=reference)
        result = await self.payment_manager.refund_payment(transaction=transaction, reference=reference, reason=reason)
        self._log_completion("refund_payment", reference=reference)
        return result

    async def reconcile_payment(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_status: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile a payment through the internal payment manager."""
        self._log_entry("reconcile_payment", reference=reference)
        result = await self.payment_manager.reconcile_payment(transaction=transaction, reference=reference, provider_status=provider_status)
        self._log_completion("reconcile_payment", reference=reference)
        return result

    async def get_payment_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the latest payment status through the internal payment manager."""
        self._log_entry("get_payment_status", reference=reference)
        result = await self.payment_manager.get_payment_status(reference=reference)
        self._log_completion("get_payment_status", reference=reference)
        return result

    def build_provider_operation(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        provider_name: str | None = None,
    ) -> Callable[[Provider], Awaitable[Any]]:
        """Create a provider callback that uses the selected provider to invoke its payment integration."""

        async def provider_operation(provider: Provider) -> Any:
            integration = self._build_provider_integration(provider=provider, provider_name=provider_name)
            if integration is None:
                raise ValidationException("No provider integration is available for the selected provider.")

            if operation == "initialize":
                amount = payload.get("amount")
                return await integration.initialize_payment(
                    tx_ref=str(payload.get("reference")),
                    amount=float(amount) if amount is not None else 0.0,
                    currency=str(payload.get("currency") or "NGN"),
                    redirect_url=payload.get("redirect_url"),
                    customer={
                        "user_id": str(payload.get("user_id")) if payload.get("user_id") is not None else None,
                        "wallet_id": str(payload.get("wallet_id")) if payload.get("wallet_id") is not None else None,
                    },
                    metadata={
                        "description": payload.get("description"),
                        "provider_name": getattr(provider, "name", None),
                    },
                )

            if operation == "verify":
                return await integration.verify_transaction(tx_ref=str(payload.get("reference")))

            raise ValidationException("Unsupported payment provider operation.")

        return provider_operation

    def _build_provider_integration(self, *, provider: Provider | None = None, provider_name: str | None = None) -> Any | None:
        """Construct the provider-specific integration implementation for the selected provider."""
        resolved_name = (provider_name or getattr(provider, "name", None) or "").strip().lower()
        if not resolved_name or "flutterwave" not in resolved_name:
            return None
        return FlutterwavePaymentService(client=FlutterwaveClient())

    async def validate_webhook_signature(
        self,
        *,
        payload: bytes,
        signature: str | None,
        timestamp: str | None,
        secret: str | None,
        provider_name: str | None = None,
    ) -> bool:
        """Validate a webhook signature through the internal webhook service."""
        self._log_entry("validate_webhook_signature", provider_name=provider_name)
        result = await self.webhook_service.validate_webhook_signature(
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
            provider_name=provider_name,
        )
        self._log_completion("validate_webhook_signature", provider_name=provider_name)
        return result

    async def process_payment_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a payment webhook through the internal webhook service."""
        self._log_entry("process_payment_webhook", provider_name=provider_name)
        result = await self.webhook_service.process_payment_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )
        self._log_completion("process_payment_webhook", provider_name=provider_name)
        return result

    async def process_virtual_account_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a virtual-account webhook through the internal webhook service."""
        self._log_entry("process_virtual_account_webhook", provider_name=provider_name)
        result = await self.webhook_service.process_virtual_account_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )
        self._log_completion("process_virtual_account_webhook", provider_name=provider_name)
        return result

    async def process_transfer_webhook(
        self,
        *,
        provider_name: str,
        event_id: str | None,
        payload: dict[str, Any],
        signature: str | None = None,
        timestamp: str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Process a transfer webhook through the internal webhook service."""
        self._log_entry("process_transfer_webhook", provider_name=provider_name)
        result = await self.webhook_service.process_transfer_webhook(
            provider_name=provider_name,
            event_id=event_id,
            payload=payload,
            signature=signature,
            timestamp=timestamp,
            secret=secret,
        )
        self._log_completion("process_transfer_webhook", provider_name=provider_name)
        return result

    async def ignore_duplicate_webhooks(self, *, event_id: str | None, provider_name: str | None) -> None:
        """Validate duplicate webhook events through the internal webhook service."""
        self._log_entry("ignore_duplicate_webhooks", provider_name=provider_name)
        await self.webhook_service.ignore_duplicate_webhooks(event_id=event_id, provider_name=provider_name)
        self._log_completion("ignore_duplicate_webhooks", provider_name=provider_name)

    async def get_transaction_user_id_by_reference(self, reference: str) -> UUID | None:
        """Get the user_id for a transaction by reference. Used for authorization checks."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            return None
        return transaction.user_id

    def _log_entry(self, operation: str, **context: Any) -> None:
        self.logger.info("payment_service_entry", extra={"operation": operation, **context})

    def _log_completion(self, operation: str, **context: Any) -> None:
        self.logger.info("payment_service_completion", extra={"operation": operation, **context})


__all__ = ["PaymentService"]
