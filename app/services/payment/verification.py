from __future__ import annotations

import logging
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import UUID

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.payment.status import normalize_payment_status
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException


class PaymentVerificationService:
    """Verify provider-confirmed payments and credit wallets once the state is trusted."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        wallet_repository: WalletRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.wallet_repository = wallet_repository
        self.logger = logger or logging.getLogger(__name__)

    async def verify_payment(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        channel: str = "card",
    ) -> dict[str, Any]:
        """Verify a payment using the provider service and credit the wallet only after a successful confirmation."""
        self.verify_transaction_reference(reference=reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Payment reference was not found.")

        if normalize_payment_status(transaction.status) == "succeeded":
            return await self._build_response(transaction)

        if normalize_payment_status(transaction.status) in {"failed", "cancelled", "refunded"}:
            raise PaymentException("The transaction cannot be verified in its current state.")

        async with self._transaction_scope():
            transaction = await self.transaction_repository.get_by_reference_for_update(reference)
            if transaction is None:
                raise ValidationException("Payment reference was not found.")
            if normalize_payment_status(transaction.status) == "succeeded":
                return await self._build_response(transaction)
            if normalize_payment_status(transaction.status) in {"failed", "cancelled", "refunded"}:
                raise PaymentException("The transaction cannot be verified in its current state.")

            self.logger.info("payment_verification_started", extra={"reference": reference, "channel": channel})
            response = await self._dispatch_provider(
                channel=channel,
                provider_operation=provider_operation,
                reference=reference,
            )
            normalized_status = normalize_payment_status(response.get("status"), default=transaction.status)
            transaction.status = normalized_status
            transaction.provider_name = response.get("provider") or transaction.provider_name
            transaction.provider_reference = response.get("provider_reference") or transaction.provider_reference
            transaction.provider_transaction_id = response.get("provider_transaction_id") or transaction.provider_transaction_id
            transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, {"verification": response})
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                metadata_payload=transaction.metadata_payload,
            )

            if normalized_status == "succeeded":
                await self._credit_wallet(transaction)

            self.logger.info("payment_verification_completed", extra={"reference": reference, "status": transaction.status})
            return await self._build_response(transaction)

    async def verify_transaction_reference(self, *, reference: str) -> None:
        """Validate that the supplied payment reference is present and well-formed."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Payment reference is required.")

    async def verify_virtual_account_payment(self, *, reference: str, provider_operation: Callable[[Provider], Awaitable[Any]] | None = None) -> dict[str, Any]:
        """Verify a virtual-account payment using the provider service."""
        return await self.verify_payment(reference=reference, provider_operation=provider_operation, channel="virtual_account")

    async def verify_bank_transfer(self, *, reference: str, provider_operation: Callable[[Provider], Awaitable[Any]] | None = None) -> dict[str, Any]:
        """Verify a bank-transfer payment using the provider service."""
        return await self.verify_payment(reference=reference, provider_operation=provider_operation, channel="bank_transfer")

    async def verify_card_payment(self, *, reference: str, provider_operation: Callable[[Provider], Awaitable[Any]] | None = None) -> dict[str, Any]:
        """Verify a card payment using the provider service."""
        return await self.verify_payment(reference=reference, provider_operation=provider_operation, channel="card")

    async def _dispatch_provider(
        self,
        *,
        channel: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None,
        reference: str,
    ) -> dict[str, Any]:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required.")

        payload = {"reference": reference, "channel": channel}
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

    async def _credit_wallet(self, transaction: Transaction) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("wallet_credit_applied") is True:
            return

        wallet = await self.wallet_repository.get_by_id_for_update(transaction.wallet_id) if transaction.wallet_id else None
        if wallet is None:
            raise ValidationException("Wallet was not found for verification crediting.")
        if wallet.available_balance < Decimal("0"):
            wallet.available_balance = Decimal("0")
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_wallet(wallet, available_balance=wallet.available_balance, ledger_balance=wallet.ledger_balance)

        metadata["wallet_credit_applied"] = True
        metadata["wallet_credit_source"] = "verification"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": normalize_payment_status(payload.get("status"), default="pending"),
            "provider": provider.name,
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
            "metadata": payload.get("metadata"),
        }

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return json.dumps(payload, default=str)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            try:
                import ast

                parsed = ast.literal_eval(payload)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                return {"value": payload}

    def _merge_metadata(self, existing: str | None, updates: dict[str, Any]) -> str | None:
        metadata = self._parse_metadata(existing)
        metadata.update(updates)
        return self._serialize_metadata(metadata)

    async def _build_response(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider": transaction.provider_name,
        }

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise
