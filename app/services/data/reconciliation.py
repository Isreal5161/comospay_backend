from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class DataReconciliationService:
    """Reconcile data purchases with provider outcomes and recover failed transactions safely."""

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

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile a single transaction against the provider system."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Transaction reference was not found.")

        self.logger.info("data_reconciliation_started", extra={"reference": reference})
        if transaction.status in {"completed", "succeeded", "settled"}:
            return await self._build_response(transaction, matched=True, status="completed")

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for reconciliation.")

        async with self._transaction_scope():
            provider_response = await self.provider_service.execute_data(
                operation=provider_operation,
                validate=lambda payload: None,
                normalize=lambda result, provider: self._normalize_provider_response(result, provider),
                payload={"reference": reference, "provider_name": provider_name, "transaction": transaction},
            )
            transaction.status = self._normalize_status(provider_response.get("status"))
            transaction.provider_name = provider_name or provider_response.get("provider") or transaction.provider_name
            transaction.provider_reference = provider_response.get("provider_reference") or transaction.provider_reference
            transaction.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction.provider_transaction_id
            transaction.external_reference = transaction.provider_reference
            transaction.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction.metadata_payload),
                    "reconciliation": {
                        "status": transaction.status,
                        "provider_response": provider_response,
                        "reconciled_at": self._now_iso(),
                    },
                }
            )
            transaction = await self.transaction_repository.update_transaction(
                transaction,
                status=transaction.status,
                provider_name=transaction.provider_name,
                provider_reference=transaction.provider_reference,
                provider_transaction_id=transaction.provider_transaction_id,
                external_reference=transaction.external_reference,
                metadata_payload=transaction.metadata_payload,
            )
            self.logger.info("data_reconciliation_matched", extra={"reference": reference, "status": transaction.status})
            return await self._build_response(transaction, matched=True, status=transaction.status)

    async def reconcile_pending_transactions(
        self,
        *,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile transactions that are still pending."""
        transactions, _ = await self.transaction_repository.get_transactions_by_status(status="pending", page_size=page_size or 100, page=1)
        results: list[dict[str, Any]] = []
        for transaction in transactions:
            try:
                results.append(
                    await self.reconcile_transaction(
                        reference=transaction.reference,
                        provider_operation=provider_operation,
                        provider_name=provider_name,
                    )
                )
            except Exception as exc:
                self.logger.warning("data_reconciliation_error", extra={"reference": transaction.reference, "error": str(exc)})
        return results

    async def reconcile_failed_transactions(
        self,
        *,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile transactions that have been marked failed and may need recovery."""
        transactions, _ = await self.transaction_repository.get_transactions_by_status(status="failed", page_size=page_size or 100, page=1)
        results: list[dict[str, Any]] = []
        for transaction in transactions:
            try:
                results.append(
                    await self.reconcile_transaction(
                        reference=transaction.reference,
                        provider_operation=provider_operation,
                        provider_name=provider_name,
                    )
                )
            except Exception as exc:
                self.logger.warning("data_reconciliation_error", extra={"reference": transaction.reference, "error": str(exc)})
        return results

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Any | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Detect missing confirmations, duplicates, or provider mismatches for a transaction."""
        if transaction is None:
            raise ValidationException("Transaction is required for discrepancy detection.")

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for discrepancy detection.")

        try:
            provider_response = await self.provider_service.execute_data(
                operation=provider_operation,
                validate=lambda payload: None,
                normalize=lambda result, provider: self._normalize_provider_response(result, provider),
                payload={"reference": transaction.reference, "provider_name": provider_name, "transaction": transaction},
            )
        except Exception as exc:
            self.logger.warning("data_reconciliation_provider_error", extra={"reference": transaction.reference, "error": str(exc)})
            return {"reference": transaction.reference, "status": "error", "discrepancy": str(exc)}

        status = self._normalize_status(provider_response.get("status"))
        internal_status = self._normalize_status(transaction.status)
        if internal_status != status:
            return {
                "reference": transaction.reference,
                "status": "mismatch",
                "internal_status": internal_status,
                "provider_status": status,
                "provider_reference": provider_response.get("provider_reference"),
            }
        return {"reference": transaction.reference, "status": "matched", "provider_status": status}

    async def reverse_failed_purchase(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Reverse a failed purchase and restore the wallet balance when a debit was applied."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        async with self._transaction_scope():
            if resolved.status in {"reversed", "cancelled"}:
                return await self._build_response(resolved, matched=True, status=resolved.status)
            if resolved.amount <= 0:
                raise ValidationException("Transaction amount must be positive.")
            await self._reverse_wallet_debit(transaction=resolved, reason=reason or "reconciliation_reversal")
            resolved.status = "reversed"
            resolved.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(resolved.metadata_payload),
                    "reversal_reason": reason or "reconciliation_reversal",
                    "reconciled_at": self._now_iso(),
                }
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("data_reversal_completed", extra={"reference": resolved.reference})
            return await self._build_response(resolved, matched=True, status=resolved.status)

    async def retry_failed_purchase(self, *, transaction: Transaction | None = None, reference: str | None = None, provider_operation: Any | None = None) -> dict[str, Any]:
        """Retry a failed data purchase by restoring a prior debit and reprocessing it."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved, matched=True, status=resolved.status)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for retries.")

        wallet = await self._get_wallet_for_transaction(resolved)
        if resolved.status in {"failed", "cancelled", "reversed"}:
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)
        self.logger.info("data_retry_started", extra={"reference": resolved.reference})
        return await self.reconcile_transaction(reference=resolved.reference, provider_operation=provider_operation, provider_name=resolved.provider_name)

    async def generate_reconciliation_report(self, *, page_size: int | None = None) -> dict[str, Any]:
        """Generate a summary of pending, failed, succeeded, and discrepant data transactions."""
        pending, _ = await self.transaction_repository.get_transactions_by_status(status="pending", page_size=page_size or 100, page=1)
        failed, _ = await self.transaction_repository.get_transactions_by_status(status="failed", page_size=page_size or 100, page=1)
        completed, _ = await self.transaction_repository.get_transactions_by_status(status="completed", page_size=page_size or 100, page=1)
        succeeded, _ = await self.transaction_repository.get_transactions_by_status(status="succeeded", page_size=page_size or 100, page=1)
        settled, _ = await self.transaction_repository.get_transactions_by_status(status="settled", page_size=page_size or 100, page=1)
        return {
            "generated_at": self._now_iso(),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "completed_count": len(completed) + len(succeeded) + len(settled),
            "records": [await self._build_response(item) for item in pending + failed + completed + succeeded + settled],
        }

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("Transaction reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Transaction reference was not found.")
        return resolved

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
        metadata["reversal_reason"] = reason or "reconciliation_reversal"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is True:
            return
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for retry.")
        wallet.available_balance = wallet.available_balance - amount
        wallet.ledger_balance = wallet.ledger_balance - amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["debit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _get_wallet_for_transaction(self, transaction: Transaction) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _build_response(self, transaction: Transaction, *, matched: bool = False, status: str | None = None) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": status or transaction.status,
            "matched": matched,
            "provider": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }

    def _normalize_provider_response(self, result: Any, provider: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": self._normalize_status(payload.get("status")),
            "provider": getattr(provider, "name", None) or payload.get("provider"),
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
        }

    def _normalize_status(self, status: str | None) -> str:
        if not status:
            return "pending"
        lowered = str(status).strip().lower()
        mapping = {
            "success": "succeeded",
            "successful": "succeeded",
            "succeeded": "succeeded",
            "completed": "completed",
            "settled": "settled",
            "failed": "failed",
            "failure": "failed",
            "error": "failed",
            "cancelled": "cancelled",
            "reversed": "reversed",
            "pending": "pending",
            "processing": "pending",
            "in-progress": "pending",
        }
        return mapping.get(lowered, lowered)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {"value": payload}
        return {"value": payload}

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return json.dumps(payload, default=str)

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Transaction reference is required.")

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _transaction_scope(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            async with self.transaction_repository.session.begin():
                yield

        return scope()


__all__ = ["DataReconciliationService"]
