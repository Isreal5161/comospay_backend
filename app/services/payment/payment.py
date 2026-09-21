from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.payment.status import normalize_payment_status
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException


class PaymentManager:
    """Manage payment lifecycle workflows with provider orchestration and wallet updates."""

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.provider_service = provider_service
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
        """Initialize a new payment, validate ownership, and dispatch it through provider orchestration."""
        self._validate_amount(amount)
        self._validate_reference(reference)
        await self._ensure_user_exists(user_id)

        existing = await self.transaction_repository.get_by_reference(reference)
        if existing is not None:
            return await self._build_transaction_response(existing)

        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        self._ensure_wallet_is_active(wallet)

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type=transaction_type,
                category=category,
                amount=amount,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount,
                status="pending",
                provider_name=provider_name,
                description=description,
                metadata_payload=metadata_payload,
            )
            transaction = await self.transaction_repository.create_transaction(transaction)

            if provider_operation is None:
                raise ValidationException("A provider operation callback is required for payment initialization.")

            self.logger.info(
                "payment_initialization_started",
                extra={"reference": reference, "user_id": str(user_id), "wallet_id": str(wallet.id), "amount": str(amount)},
            )

            provider_response = await self.provider_service.execute_payment(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "reference": reference,
                    "amount": str(amount),
                    "currency": currency,
                    "wallet_id": str(wallet.id),
                    "user_id": str(user_id),
                    "description": description,
                    "redirect_url": redirect_url,
                },
            )
            provider_name_value = provider_response.get("provider")
            if isinstance(provider_name_value, dict):
                transaction.provider_name = provider_name_value.get("name") or provider_name
            else:
                transaction.provider_name = provider_name_value or provider_name
            transaction.provider_reference = provider_response.get("provider_reference")
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id")
            transaction.external_reference = provider_response.get("provider_reference")
            transaction.metadata_payload = self._serialize_metadata(provider_response)
            transaction.status = normalize_payment_status(provider_response.get("status"), default="pending")
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                external_reference=transaction.external_reference,
                metadata_payload=transaction.metadata_payload,
                status=transaction.status,
            )

            self.logger.info(
                "payment_initialization_completed",
                extra={"reference": reference, "status": transaction.status, "provider": transaction.provider_name},
            )
            return await self._build_transaction_response(transaction)

    async def verify_payment(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Verify a payment state and reconcile it with the provider when possible."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Payment reference was not found.")

        if normalize_payment_status(transaction.status) == "succeeded":
            return await self._build_transaction_response(transaction)

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for payment verification.")

        async with self._transaction_scope():
            self.logger.info("payment_verification_started", extra={"reference": reference})
            provider_response = await self.provider_service.execute_payment(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={"reference": reference},
            )
            new_status = normalize_payment_status(provider_response.get("status"), default=transaction.status)
            transaction.status = new_status
            transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction.provider_transaction_id
            transaction.metadata_payload = self._serialize_metadata(provider_response)
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                metadata_payload=transaction.metadata_payload,
            )

            if transaction.status == "succeeded":
                await self.process_successful_payment(transaction=transaction)

            self.logger.info("payment_verification_completed", extra={"reference": reference, "status": transaction.status})
            return await self._build_transaction_response(transaction)

    async def process_successful_payment(self, *, transaction: Transaction | None = None, reference: str | None = None) -> dict[str, Any]:
        """Credit the wallet once for a successful payment and prevent double-crediting."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if normalize_payment_status(resolved.status) == "succeeded":
            return await self._build_transaction_response(resolved)
        if normalize_payment_status(resolved.status) in {"failed", "cancelled", "refunded"}:
            raise PaymentException("The transaction cannot be completed in its current state.")

        wallet = await self._resolve_wallet(user_id=resolved.user_id, wallet_id=resolved.wallet_id)
        async with self._transaction_scope():
            if wallet is None:
                raise ValidationException("Payment wallet could not be resolved.")
            wallet.available_balance = wallet.available_balance + resolved.amount
            wallet.ledger_balance = wallet.ledger_balance + resolved.amount
            wallet = await self.wallet_repository.update_wallet(wallet, available_balance=wallet.available_balance, ledger_balance=wallet.ledger_balance)
            resolved.status = "succeeded"
            resolved.metadata_payload = self._serialize_metadata({"credit_applied": True, "wallet_id": str(wallet.id)})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info(
                "payment_success_processed",
                extra={"reference": resolved.reference, "wallet_id": str(wallet.id), "amount": str(resolved.amount)},
            )
            return await self._build_transaction_response(resolved)

    async def process_failed_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Mark a payment as failed without crediting the wallet."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if normalize_payment_status(resolved.status) in {"failed", "cancelled", "refunded"}:
            return await self._build_transaction_response(resolved)

        async with self._transaction_scope():
            resolved.status = "failed"
            resolved.metadata_payload = self._serialize_metadata({"failure_reason": reason or "payment_failed"})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_failure_processed", extra={"reference": resolved.reference, "reason": reason})
            return await self._build_transaction_response(resolved)

    async def cancel_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Cancel a pending payment when the user or provider aborts the flow."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if normalize_payment_status(resolved.status) in {"cancelled", "failed", "succeeded"}:
            return await self._build_transaction_response(resolved)

        async with self._transaction_scope():
            resolved.status = "cancelled"
            resolved.metadata_payload = self._serialize_metadata({"cancellation_reason": reason or "cancelled_by_user"})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_cancelled", extra={"reference": resolved.reference, "reason": reason})
            return await self._build_transaction_response(resolved)

    async def refund_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Refund a completed payment by debiting the wallet and marking the transaction refunded."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"refunded"}:
            return await self._build_transaction_response(resolved)
        if normalize_payment_status(resolved.status) != "succeeded":
            raise PaymentException("Only successful payments can be refunded.")

        wallet = await self._resolve_wallet(user_id=resolved.user_id, wallet_id=resolved.wallet_id)
        async with self._transaction_scope():
            if wallet is None:
                raise ValidationException("Payment wallet could not be resolved.")
            wallet.available_balance = wallet.available_balance - resolved.amount
            wallet.ledger_balance = wallet.ledger_balance - resolved.amount
            wallet = await self.wallet_repository.update_wallet(wallet, available_balance=wallet.available_balance, ledger_balance=wallet.ledger_balance)
            resolved.status = "refunded"
            resolved.metadata_payload = self._serialize_metadata({"refund_reason": reason or "refund_requested"})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_refund_processed", extra={"reference": resolved.reference, "amount": str(resolved.amount)})
            return await self._build_transaction_response(resolved)

    async def reconcile_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, provider_status: str | None = None) -> dict[str, Any]:
        """Reconcile the payment state with the latest provider status."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        async with self._transaction_scope():
            resolved.status = normalize_payment_status(provider_status, default=resolved.status)
            resolved.metadata_payload = self._serialize_metadata({"reconciled": True, "provider_status": provider_status})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_reconciliation_completed", extra={"reference": resolved.reference, "status": resolved.status})
            return await self._build_transaction_response(resolved)

    async def get_payment_status(self, *, reference: str) -> dict[str, Any]:
        """Fetch the current payment status for a reference."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Payment reference was not found.")
        return await self._build_transaction_response(transaction)

    async def _ensure_user_exists(self, user_id: UUID) -> None:
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User was not found.")

    async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet | None:
        if wallet_id is not None:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
            if wallet is not None and wallet.user_id != user_id:
                raise ValidationException("Wallet ownership does not match the provided user.")
            return wallet
        return await self.wallet_repository.get_user_wallet(user_id=user_id)

    def _ensure_wallet_is_active(self, wallet: Wallet | None) -> None:
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        if not wallet.is_active or wallet.is_suspended or wallet.is_frozen:
            raise PaymentException("Wallet is not available for payments.")

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("A payment reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Payment reference was not found.")
        return resolved

    async def _build_transaction_response(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "wallet_id": str(transaction.wallet_id) if transaction.wallet_id else None,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }

    def _validate_amount(self, amount: Decimal) -> None:
        if amount <= 0:
            raise ValidationException("Payment amount must be greater than zero.")

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Payment reference is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}

        nested_payload = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        flattened_payload = {**payload, **nested_payload}

        payment_link = self._extract_url(flattened_payload, "payment_link", "link", "hosted_link")
        checkout_url = self._extract_url(flattened_payload, "checkout_url", "payment_link", "link", "hosted_link")
        authorization_url = self._extract_url(flattened_payload, "authorization_url")

        normalized = {
            "status": normalize_payment_status(payload.get("status"), default="pending"),
            "provider": provider.name,
            "provider_reference": payload.get("provider_reference") or payload.get("reference") or nested_payload.get("tx_ref") or payload.get("tx_ref"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id") or nested_payload.get("id"),
            "message": payload.get("message"),
            "metadata": payload.get("metadata") or nested_payload,
        }

        if payment_link:
            normalized["payment_link"] = payment_link
        if checkout_url:
            normalized["checkout_url"] = checkout_url
        if authorization_url:
            normalized["authorization_url"] = authorization_url
        elif payment_link and "authorization_url" not in normalized:
            normalized["authorization_url"] = None

        return normalized

    def _extract_url(self, payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

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
