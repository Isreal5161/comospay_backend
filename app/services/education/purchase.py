from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.services.wallet_service import WalletService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class EducationPurchaseService:
    """Manage education service purchase workflows using injected wallet, provider, and repository services."""

    def __init__(
        self,
        *,
        wallet_service: WalletService,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_service = wallet_service
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
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
        """Create a pending education purchase and dispatch it through provider orchestration."""
        amount_value = self._normalize_amount(amount)
        self._validate_purchase_request(
            user_id=user_id,
            examination_type=examination_type,
            provider=provider,
            candidate_number=candidate_number,
            amount=amount_value,
            quantity=quantity,
            examination_year=examination_year,
            transaction_pin=transaction_pin,
        )

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for education purchases.")

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)

        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not active for education purchase.")
        if wallet.available_balance < amount_value:
            raise WalletException("Insufficient wallet balance for education purchase.")

        reference = self.generate_reference()
        self.logger.info(
            "education_purchase_started",
            extra={
                "reference": reference,
                "user_id": str(user_id),
                "provider": provider,
                "candidate_number": candidate_number,
                "examination_type": examination_type,
                "examination_year": str(examination_year),
                "amount": str(amount_value),
                "quantity": quantity,
            },
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="education_purchase",
                category="education",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"Education service purchase for {candidate_number}",
                metadata_payload=self._serialize_metadata(
                    {
                        "provider": provider,
                        "examination_type": examination_type,
                        "candidate_number": candidate_number,
                        "quantity": quantity,
                        "examination_year": str(examination_year),
                        "wallet_id": str(wallet.id),
                        "requested_amount": str(amount_value),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_education_purchase(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def process_education_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending education purchase to the provider and update its outcome."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for education processing.")

        self.logger.info("education_purchase_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = await self.provider_service.execute_education(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "reference": resolved.reference,
                    "provider": self._extract_provider(resolved),
                    "examination_type": self._extract_examination_type(resolved),
                    "candidate_number": self._extract_candidate_number(resolved),
                    "quantity": self._extract_quantity(resolved),
                    "examination_year": self._extract_examination_year(resolved),
                    "amount": str(resolved.amount),
                    "currency": resolved.currency,
                    "wallet_id": str(resolved.wallet_id) if resolved.wallet_id else None,
                    "user_id": str(resolved.user_id),
                    "description": resolved.description,
                    "metadata_payload": metadata_payload,
                },
            )
        except Exception as exc:
            await self._handle_provider_failure(transaction=resolved, reason=str(exc))
            raise PaymentException(detail=str(exc)) from exc

        status = self._normalize_status(provider_response.get("status"))
        async with self._transaction_scope():
            transaction_record = await self.transaction_repository.get_by_reference(resolved.reference)
            if transaction_record is None:
                raise ValidationException("Purchase transaction was not found.")

            transaction_record.status = status
            transaction_record.provider_name = provider_name or provider_response.get("provider", {}).get("name") or transaction_record.provider_name
            transaction_record.provider_reference = provider_response.get("provider_reference") or transaction_record.provider_reference
            transaction_record.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction_record.provider_transaction_id
            transaction_record.external_reference = transaction_record.provider_reference
            transaction_record.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction_record.metadata_payload),
                    "provider_response": provider_response,
                    "candidate_number": self._extract_candidate_number(transaction_record),
                    "examination_type": self._extract_examination_type(transaction_record),
                    "examination_year": self._extract_examination_year(transaction_record),
                    "quantity": self._extract_quantity(transaction_record),
                    "attempted": True,
                }
            )
            transaction_record = await self.transaction_repository.update_transaction(
                transaction_record,
                status=transaction_record.status,
                provider_name=transaction_record.provider_name,
                provider_reference=transaction_record.provider_reference,
                provider_transaction_id=transaction_record.provider_transaction_id,
                external_reference=transaction_record.external_reference,
                metadata_payload=transaction_record.metadata_payload,
            )

            if status in {"failed", "cancelled", "reversed"}:
                await self._handle_provider_failure(
                    transaction=transaction_record,
                    reason=provider_response.get("message") or "Provider reported a failed education purchase.",
                )
            elif status in {"succeeded", "completed", "settled"}:
                self.logger.info("education_purchase_succeeded", extra={"reference": transaction_record.reference})
            else:
                self.logger.info(
                    "education_purchase_pending",
                    extra={"reference": transaction_record.reference, "status": status},
                )

            return await self._build_response(transaction_record)

    async def retry_education_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a pending or failed education purchase through provider orchestration."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for education retries.")

        wallet = await self._get_wallet_for_transaction(resolved)
        if resolved.status in {"failed", "cancelled", "reversed"}:
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)

        self.logger.info("education_purchase_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_education_purchase(
            transaction=resolved,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the current lifecycle state of an education purchase."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Education purchase reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Education purchase reference was not found.")
        return await self._build_response(transaction)

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Fetch detailed education purchase transaction data."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Education purchase reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self) -> str:
        """Create a unique reference for a new education purchase."""
        return f"education-{uuid4().hex[:12]}"

    async def _ensure_user_exists(self, user_id: UUID) -> None:
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User was not found.")

    async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet:
        if wallet_id is not None:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
            if wallet is None:
                raise ValidationException("Wallet was not found.")
            if wallet.user_id != user_id:
                raise ValidationException("Wallet ownership does not match the provided user.")
            return wallet
        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _get_wallet_for_transaction(self, transaction: Transaction) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for education purchase.")
        wallet.available_balance = wallet.available_balance - amount
        wallet.ledger_balance = wallet.ledger_balance - amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata = self._parse_metadata(transaction.metadata_payload)
        metadata["debit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _reverse_wallet_debit(self, *, transaction: Transaction, reason: str | None = None) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is not True:
            metadata["reversal_reason"] = reason or "wallet_debit_not_required"
            transaction.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
            return
        if transaction.wallet_id is None:
            raise ValidationException("Wallet reference is missing for reversal.")

        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found for reversal.")
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["debit_applied"] = False
        metadata["reversal_applied"] = True
        metadata["reversal_reason"] = reason or "education_purchase_reversed"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        self.logger.warning("education_purchase_failed", extra={"reference": transaction.reference, "reason": reason})
        if transaction.status not in {"failed", "cancelled", "reversed"}:
            transaction.status = "failed"
            transaction.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction.metadata_payload),
                    "failure_reason": reason,
                }
            )
            await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)

    async def _resolve_transaction(self, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Education purchase transaction reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference.strip())
        if resolved is None:
            raise ValidationException("Education purchase transaction was not found.")
        return resolved

    def _validate_purchase_request(
        self,
        *,
        user_id: UUID,
        examination_type: str,
        provider: str,
        candidate_number: str,
        amount: Decimal,
        quantity: int,
        examination_year: int | str,
        transaction_pin: str,
    ) -> None:
        if user_id is None:
            raise ValidationException("User ID is required.")
        if not examination_type or not isinstance(examination_type, str) or not examination_type.strip():
            raise ValidationException("Examination type is required.")
        if not provider or not isinstance(provider, str) or not provider.strip():
            raise ValidationException("Education provider is required.")
        if not candidate_number or not isinstance(candidate_number, str) or not candidate_number.strip():
            raise ValidationException("Candidate number is required.")
        if amount <= Decimal("0"):
            raise ValidationException("Amount must be greater than zero.")
        if quantity < 1:
            raise ValidationException("Quantity must be at least 1.")
        if not examination_year or (isinstance(examination_year, str) and not examination_year.strip()):
            raise ValidationException("Examination year is required.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    async def _build_response(self, transaction: Transaction, include_metadata: bool = False) -> dict[str, Any]:
        response: dict[str, Any] = {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "description": transaction.description,
            "created_at": transaction.created_at.isoformat(),
            "updated_at": transaction.updated_at.isoformat(),
        }
        if include_metadata:
            response["metadata_payload"] = self._parse_metadata(transaction.metadata_payload)
        return response

    def _extract_provider(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("provider") or "").strip()

    def _extract_examination_type(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("examination_type") or "").strip()

    def _extract_candidate_number(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("candidate_number") or "").strip()

    def _extract_quantity(self, transaction: Transaction) -> int:
        payload = self._parse_metadata(transaction.metadata_payload)
        quantity = payload.get("quantity")
        return int(quantity) if isinstance(quantity, int) else int(str(quantity)) if quantity is not None else 1

    def _extract_examination_year(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("examination_year") or "").strip()

    def _parse_metadata(self, metadata_payload: str | None) -> dict[str, Any]:
        if not metadata_payload or not isinstance(metadata_payload, str):
            return {}
        try:
            return json.loads(metadata_payload)
        except Exception:
            return {}

    def _serialize_metadata(self, payload: dict[str, Any]) -> str:
        try:
            return json.dumps(payload)
        except Exception:
            return "{}"

    def _normalize_status(self, status: Any) -> str:
        if not status or not isinstance(status, str):
            return "pending"
        normalized = status.strip().lower()
        if normalized in {"success", "succeeded", "completed", "settled"}:
            return "succeeded"
        if normalized in {"failed", "failure", "cancelled", "cancelled", "reversed"}:
            return "failed"
        if normalized in {"pending", "processing", "queued"}:
            return "pending"
        return "pending"

    def _transaction_scope(self):
        return self.transaction_repository.session.begin()


__all__ = ["EducationPurchaseService"]
