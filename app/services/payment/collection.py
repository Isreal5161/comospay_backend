from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException


class PaymentCollectionService:
    """Collect payments from customers and coordinate provider execution safely."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.logger = logger or logging.getLogger(__name__)

    async def initialize_payment(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str = "NGN",
        channel: str = "card",
        reference: str | None = None,
        description: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a new collection request and dispatch it through the provider facade."""
        self.validate_payment_request(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            channel=channel,
        )

        resolved_reference = reference or await self.generate_payment_reference()
        existing = await self.transaction_repository.get_by_reference(resolved_reference)
        if existing is not None:
            raise PaymentException("Duplicate payment initialization detected for the supplied reference.")

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        if wallet.user_id != user_id:
            raise ValidationException("Wallet does not belong to the supplied user.")

        transaction = await self.create_pending_transaction(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            reference=resolved_reference,
            channel=channel,
            description=description,
            metadata_payload=metadata_payload,
        )

        self.logger.info(
            "payment_collection_initializing",
            extra={"reference": resolved_reference, "channel": channel, "wallet_id": str(wallet_id), "amount": str(amount)},
        )

        try:
            async with self._transaction_scope():
                response = await self._dispatch_provider(
                    channel=channel,
                    provider_operation=provider_operation,
                    reference=resolved_reference,
                    amount=amount,
                    currency=currency,
                    user_id=user_id,
                    wallet_id=wallet_id,
                    description=description,
                )
                transaction.status = response.get("status", "pending")
                transaction.provider_name = response.get("provider") or transaction.provider_name
                transaction.provider_reference = response.get("provider_reference") or transaction.provider_reference
                transaction.provider_transaction_id = response.get("provider_transaction_id") or transaction.provider_transaction_id
                transaction.metadata_payload = self._serialize_metadata({
                    "channel": channel,
                    "provider_response": response,
                    "metadata": metadata_payload,
                })
                transaction = await self.transaction_repository.update_transaction(
                    transaction,
                    status=transaction.status,
                    provider_name=transaction.provider_name,
                    provider_reference=transaction.provider_reference,
                    provider_transaction_id=transaction.provider_transaction_id,
                    metadata_payload=transaction.metadata_payload,
                )
        except Exception as exc:
            transaction.status = "failed"
            transaction.metadata_payload = self._serialize_metadata({"channel": channel, "error": str(exc)})
            await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
            self.logger.warning(
                "payment_collection_failed",
                extra={"reference": resolved_reference, "channel": channel, "error": str(exc)},
            )
            raise

        self.logger.info(
            "payment_collection_initialized",
            extra={"reference": resolved_reference, "channel": channel, "status": transaction.status},
        )
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "channel": channel,
            "provider": transaction.provider_name,
        }

    async def initialize_card_payment(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str = "NGN",
        reference: str | None = None,
        description: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a card-based payment collection flow."""
        return await self.initialize_payment(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            channel="card",
            reference=reference,
            description=description,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )

    async def initialize_bank_transfer(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str = "NGN",
        reference: str | None = None,
        description: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a bank-transfer payment collection flow."""
        return await self.initialize_payment(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            channel="bank_transfer",
            reference=reference,
            description=description,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )

    async def initialize_virtual_account_payment(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str = "NGN",
        reference: str | None = None,
        description: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a virtual-account payment collection flow."""
        return await self.initialize_payment(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            currency=currency,
            channel="virtual_account",
            reference=reference,
            description=description,
            provider_operation=provider_operation,
            metadata_payload=metadata_payload,
        )

    async def generate_payment_reference(self) -> str:
        """Generate a unique payment reference for a collection request."""
        while True:
            reference = f"PAY-{uuid4().hex[:12].upper()}"
            existing = await self.transaction_repository.get_by_reference(reference)
            if existing is None:
                return reference

    async def create_pending_transaction(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str,
        reference: str,
        channel: str,
        description: str | None,
        metadata_payload: str | None,
    ) -> Transaction:
        """Create a pending transaction record before dispatching to a provider."""
        transaction = Transaction(
            reference=reference,
            user_id=user_id,
            wallet_id=wallet_id,
            transaction_type="payment_collection",
            category="payment",
            amount=amount,
            currency=currency,
            charges=Decimal("0"),
            total_amount=amount,
            status="pending",
            provider_name=None,
            description=description,
            metadata_payload=self._serialize_metadata({"channel": channel, "metadata": metadata_payload}),
        )
        return await self.transaction_repository.create_transaction(transaction)

    def validate_payment_request(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str,
        channel: str,
    ) -> None:
        """Validate the collection request before creating a transaction or dispatching a provider."""
        if not user_id:
            raise ValidationException("User identifier is required.")
        if not wallet_id:
            raise ValidationException("Wallet identifier is required.")
        if amount <= 0:
            raise ValidationException("Payment amount must be greater than zero.")
        if not currency or not isinstance(currency, str):
            raise ValidationException("Currency is required.")
        if channel not in {"card", "bank_transfer", "virtual_account"}:
            raise ValidationException("Unsupported payment channel.")

    async def _dispatch_provider(
        self,
        *,
        channel: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None,
        reference: str,
        amount: Decimal,
        currency: str,
        user_id: UUID,
        wallet_id: UUID,
        description: str | None,
    ) -> dict[str, Any]:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required.")

        payload = {
            "reference": reference,
            "amount": str(amount),
            "currency": currency,
            "user_id": str(user_id),
            "wallet_id": str(wallet_id),
            "description": description,
            "channel": channel,
        }

        self.logger.info(
            "payment_provider_selected",
            extra={"channel": channel, "reference": reference, "provider": "provider_service"},
        )
        if channel == "virtual_account":
            return await self.provider_service.execute_virtual_account(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload=payload,
            )
        if channel == "bank_transfer":
            return await self.provider_service.execute_transfer(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload=payload,
            )
        return await self.provider_service.execute_payment(
            operation=provider_operation,
            validate=self._validate_provider_payload,
            normalize=self._normalize_provider_response,
            payload=payload,
        )

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": str(payload.get("status", "pending")).lower(),
            "provider": provider.name,
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
            "metadata": payload.get("metadata"),
        }

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return str(payload)

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise
