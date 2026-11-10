from __future__ import annotations

import json
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
from app.services.ledger_service import LedgerService
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
        ledger_service: LedgerService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.provider_service = provider_service
        self.ledger_service = ledger_service
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
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for payment initialization.")

        async with self._transaction_scope():
            await self._ensure_user_exists(user_id)
            existing = await self.transaction_repository.get_by_reference_for_update(reference)
            if existing is not None:
                return await self._build_transaction_response(existing)

            wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
            self._ensure_wallet_is_active(wallet)
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
            transaction_wallet_id = wallet.id

        self.logger.info(
            "payment_initialization_started",
            extra={"reference": reference, "user_id": str(user_id), "wallet_id": str(transaction_wallet_id), "amount": str(amount)},
        )
        try:
            provider_response = await self.provider_service.execute_payment(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "reference": reference,
                    "amount": str(amount),
                    "currency": currency,
                    "wallet_id": str(transaction_wallet_id),
                    "user_id": str(user_id),
                    "description": description,
                    "redirect_url": redirect_url,
                },
            )
        except Exception as exc:
            self.logger.error(
                "payment_initialization_outcome_uncertain",
                extra={"reference": reference, "error_type": type(exc).__name__},
            )
            raise PaymentException(
                "Payment initialization outcome could not be confirmed. Check transaction status before retrying."
            ) from exc

        async with self._transaction_scope():
            transaction = await self.transaction_repository.get_by_reference_for_update(reference)
            if transaction is None:
                raise ValidationException("Payment reference was not found.")
            provider_name_value = provider_response.get("provider")
            if isinstance(provider_name_value, dict):
                transaction.provider_name = provider_name_value.get("name") or provider_name
            else:
                transaction.provider_name = provider_name_value or provider_name
            transaction.provider_reference = provider_response.get("provider_reference")
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id")
            transaction.external_reference = provider_response.get("provider_reference")
            transaction.metadata_payload = self._merge_payment_metadata(
                transaction.metadata_payload,
                {
                    "initialization": {
                        "status": normalize_payment_status(provider_response.get("status"), default="pending"),
                        "provider": transaction.provider_name,
                        "provider_reference": transaction.provider_reference,
                        "provider_transaction_id": transaction.provider_transaction_id,
                        "payment_link": provider_response.get("payment_link"),
                        "checkout_url": provider_response.get("checkout_url"),
                        "authorization_url": provider_response.get("authorization_url"),
                    }
                },
            )
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
            if transaction.status == "succeeded":
                await self._settle_success_locked(transaction)

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
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for payment verification.")

        async with self._transaction_scope():
            transaction = await self.transaction_repository.get_by_reference_for_update(reference)
            if transaction is None:
                raise ValidationException("Payment reference was not found.")
            if normalize_payment_status(transaction.status) == "succeeded":
                await self._settle_success_locked(transaction)
                return await self._build_transaction_response(transaction)
            if normalize_payment_status(transaction.status) in {"failed", "cancelled", "refunded", "reversed"}:
                raise PaymentException("The transaction cannot be verified in its current state.")

            self.logger.info("payment_verification_started", extra={"reference": reference})
            provider_response = await self.provider_service.execute_payment(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={"reference": reference},
            )
            new_status = normalize_payment_status(provider_response.get("status"), default=transaction.status)
            transaction.status = new_status
            transaction.provider_name = provider_response.get("provider") or transaction.provider_name
            transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction.provider_transaction_id
            transaction.metadata_payload = self._merge_payment_metadata(
                transaction.metadata_payload,
                {
                    "verification": {
                        "status": new_status,
                        "provider": transaction.provider_name,
                        "provider_reference": transaction.provider_reference,
                        "provider_transaction_id": transaction.provider_transaction_id,
                    }
                },
            )
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                metadata_payload=transaction.metadata_payload,
            )

            if transaction.status == "succeeded":
                await self._settle_success_locked(transaction)

            self.logger.info("payment_verification_completed", extra={"reference": reference, "status": transaction.status})
            return await self._build_transaction_response(transaction)

    async def process_successful_payment(self, *, transaction: Transaction | None = None, reference: str | None = None) -> dict[str, Any]:
        """Credit the wallet once for a successful payment and prevent double-crediting."""
        async with self._transaction_scope():
            resolved = await self._get_transaction_for_update(transaction=transaction, reference=reference)
            if normalize_payment_status(resolved.status) in {"failed", "cancelled", "refunded", "reversed"}:
                raise PaymentException("The transaction cannot be completed in its current state.")
            await self._settle_success_locked(resolved)
            return await self._build_transaction_response(resolved)

    async def _settle_success_locked(self, transaction: Transaction) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("wallet_credit_applied") is True or metadata.get("credit_applied") is True:
            status_changed = transaction.status != "succeeded"
            transaction.status = "succeeded"
            if metadata.get("wallet_credit_applied") is not True:
                metadata["wallet_credit_applied"] = True
                transaction.metadata_payload = self._serialize_metadata(metadata)
                status_changed = True
            if status_changed:
                await self.transaction_repository.update_transaction(
                    transaction,
                    status=transaction.status,
                    metadata_payload=transaction.metadata_payload,
                )
            return
        if self.ledger_service is None:
            raise ValidationException("Payment ledger service is unavailable.")
        if transaction.wallet_id is None:
            raise ValidationException("Payment wallet could not be resolved.")

        wallet = await self.wallet_repository.get_by_id_for_update(transaction.wallet_id)
        if wallet is None or wallet.user_id != transaction.user_id:
            raise ValidationException("Payment wallet could not be resolved.")

        opening_balance = wallet.available_balance
        wallet.available_balance += transaction.amount
        wallet.ledger_balance += transaction.amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        await self.ledger_service.create_credit_entry(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            transaction_reference=transaction.reference,
            transaction_type="PAYMENT_CREDIT",
            opening_balance=opening_balance,
            amount=transaction.amount,
            currency=wallet.currency,
            description=transaction.description or "Payment wallet credit",
            related_transaction_id=transaction.id,
            created_by="payment",
            status="POSTED",
            metadata={"source": "payment_settlement"},
        )

        metadata.update({"wallet_credit_applied": True, "wallet_credit_source": "payment"})
        transaction.status = "succeeded"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(
            transaction,
            status=transaction.status,
            metadata_payload=transaction.metadata_payload,
        )

    async def _get_transaction_for_update(
        self,
        *,
        transaction: Transaction | None,
        reference: str | None,
    ) -> Transaction:
        key = transaction.reference if transaction is not None else reference
        if not key:
            raise ValidationException("A payment reference is required.")
        resolved = await self.transaction_repository.get_by_reference_for_update(key)
        if resolved is None:
            raise ValidationException("Payment reference was not found.")
        return resolved

    async def process_failed_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Mark a payment as failed without crediting the wallet."""
        async with self._transaction_scope():
            resolved = await self._get_transaction_for_update(transaction=transaction, reference=reference)
            if normalize_payment_status(resolved.status) in {"succeeded", "completed", "settled", "failed", "cancelled", "refunded", "reversed"}:
                return await self._build_transaction_response(resolved)
            resolved.status = "failed"
            resolved.metadata_payload = self._merge_payment_metadata(
                resolved.metadata_payload,
                {"failure_reason": reason or "payment_failed"},
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_failure_processed", extra={"reference": resolved.reference, "reason": reason})
            return await self._build_transaction_response(resolved)

    async def cancel_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Cancel a pending payment when the user or provider aborts the flow."""
        async with self._transaction_scope():
            resolved = await self._get_transaction_for_update(transaction=transaction, reference=reference)
            if normalize_payment_status(resolved.status) in {"cancelled", "failed", "succeeded", "completed", "settled", "refunded", "reversed"}:
                return await self._build_transaction_response(resolved)
            resolved.status = "cancelled"
            resolved.metadata_payload = self._merge_payment_metadata(
                resolved.metadata_payload,
                {"cancellation_reason": reason or "cancelled_by_user"},
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_cancelled", extra={"reference": resolved.reference, "reason": reason})
            return await self._build_transaction_response(resolved)

    async def refund_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Refund a completed payment by debiting the wallet and marking the transaction refunded."""
        async with self._transaction_scope():
            resolved = await self._get_transaction_for_update(transaction=transaction, reference=reference)
            if normalize_payment_status(resolved.status) == "refunded":
                return await self._build_transaction_response(resolved)
            if normalize_payment_status(resolved.status) != "succeeded":
                raise PaymentException("Only successful payments can be refunded.")
            if resolved.wallet_id is None:
                raise ValidationException("Payment wallet could not be resolved.")
            wallet = await self.wallet_repository.get_by_id_for_update(resolved.wallet_id)
            if wallet is None or wallet.user_id != resolved.user_id:
                raise ValidationException("Payment wallet could not be resolved.")
            if self.ledger_service is None:
                raise ValidationException("Payment ledger service is unavailable.")
            if wallet.available_balance < resolved.amount:
                raise PaymentException("Wallet funds are insufficient to apply this refund.")
            opening_balance = wallet.available_balance
            wallet.available_balance = wallet.available_balance - resolved.amount
            wallet.ledger_balance = wallet.ledger_balance - resolved.amount
            await self.wallet_repository.update_balance_fields(
                wallet,
                available_balance=wallet.available_balance,
                ledger_balance=wallet.ledger_balance,
            )
            await self.ledger_service.create_debit_entry(
                wallet_id=wallet.id,
                user_id=wallet.user_id,
                transaction_reference=resolved.reference,
                transaction_type="PAYMENT_REFUND",
                opening_balance=opening_balance,
                amount=resolved.amount,
                currency=wallet.currency,
                description=reason or "Payment refund",
                related_transaction_id=resolved.id,
                created_by="payment",
                status="POSTED",
                metadata={"source": "payment_refund"},
            )
            resolved.status = "refunded"
            resolved.metadata_payload = self._merge_payment_metadata(
                resolved.metadata_payload,
                {"refund_applied": True, "refund_reason": reason or "refund_requested"},
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("payment_refund_processed", extra={"reference": resolved.reference, "amount": str(resolved.amount)})
            return await self._build_transaction_response(resolved)

    async def reconcile_payment(self, *, transaction: Transaction | None = None, reference: str | None = None, provider_status: str | None = None) -> dict[str, Any]:
        """Reconcile the payment state with the latest provider status."""
        async with self._transaction_scope():
            resolved = await self._get_transaction_for_update(transaction=transaction, reference=reference)
            current_status = normalize_payment_status(resolved.status)
            incoming_status = normalize_payment_status(provider_status, default=resolved.status)
            terminal_statuses = {"succeeded", "failed", "cancelled", "refunded", "reversed"}
            if current_status in terminal_statuses and incoming_status != current_status:
                incoming_status = current_status
            resolved.status = incoming_status
            resolved.metadata_payload = self._merge_payment_metadata(
                resolved.metadata_payload,
                {"reconciled": True, "provider_status": provider_status},
            )
            if incoming_status == "succeeded":
                await self._settle_success_locked(resolved)
            else:
                resolved = await self.transaction_repository.update_transaction(
                    resolved,
                    status=resolved.status,
                    metadata_payload=resolved.metadata_payload,
                )
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
        return json.dumps(payload, default=str)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _merge_payment_metadata(self, existing: str | None, updates: dict[str, Any]) -> str:
        metadata = self._parse_metadata(existing)
        metadata.update(updates)
        return json.dumps(metadata, default=str)

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        session = self.transaction_repository.session
        if session.in_transaction():
            try:
                yield
            except Exception:
                raise
            else:
                await session.commit()
            return
        try:
            async with session.begin():
                yield
        except Exception:
            raise
