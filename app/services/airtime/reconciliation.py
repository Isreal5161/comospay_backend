from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class AirtimeReconciliationService:
    """Reconcile airtime transactions with provider state and maintain an audit trail."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        wallet_repository: WalletRepository,
        logger: logging.Logger | None = None,
        batch_size: int = 100,
    ) -> None:
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.wallet_repository = wallet_repository
        self.logger = logger or logging.getLogger(__name__)
        self.batch_size = max(1, batch_size)

    async def reconcile_transaction(
        self,
        *,
        reference: str,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile one airtime transaction with the provider and update the internal state."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Transaction reference was not found.")

        async with self._transaction_scope():
            self.logger.info("airtime_reconciliation_started", extra={"reference": reference})
            discrepancy = await self.detect_discrepancies(
                transaction=transaction,
                provider_operation=provider_operation,
                provider_name=provider_name,
            )
            if discrepancy is None:
                transaction.metadata_payload = self._update_audit_entry(transaction.metadata_payload, {"event": "reconciled", "status": transaction.status})
                transaction = await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
                self.logger.info("airtime_reconciliation_completed", extra={"reference": reference, "status": transaction.status})
                return await self._build_response(transaction, discrepancy=None)

            if discrepancy["type"] == "failed_purchase":
                result = await self.reverse_failed_purchase(transaction=transaction, reason=discrepancy.get("message"), provider_name=provider_name)
                self.logger.info("airtime_reconciliation_completed", extra={"reference": reference, "status": result["status"]})
                return result

            if discrepancy["type"] == "duplicate_purchase":
                transaction.status = "duplicate"
                transaction.metadata_payload = self._update_audit_entry(transaction.metadata_payload, {"event": "duplicate_purchase", "message": discrepancy.get("message")})
                transaction = await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                return await self._build_response(transaction, discrepancy=discrepancy)

            if discrepancy["type"] == "successful_uncredited_purchase":
                transaction.status = "succeeded"
                transaction.metadata_payload = self._update_audit_entry(transaction.metadata_payload, {"event": "wallet_credit_applied", "provider_status": discrepancy.get("provider_status")})
                wallet = await self._credit_wallet_for_transaction(transaction)
                transaction = await self.transaction_repository.update_transaction(
                    transaction,
                    status=transaction.status,
                    metadata_payload=transaction.metadata_payload,
                )
                self.logger.info(
                    "airtime_reconciliation_completed",
                    extra={"reference": reference, "status": transaction.status, "wallet_id": str(wallet.id) if wallet else None},
                )
                return await self._build_response(transaction, discrepancy=discrepancy)

            transaction.metadata_payload = self._update_audit_entry(transaction.metadata_payload, {"event": "reconciled", "discrepancy": discrepancy})
            transaction = await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
            return await self._build_response(transaction, discrepancy=discrepancy)

    async def reconcile_pending_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile all pending airtime transactions in paginated batches."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            transactions, total = await self.transaction_repository.get_transactions_by_status(
                status="pending",
                page=page,
                page_size=page_size or self.batch_size,
            )
            if not transactions:
                break
            for transaction in transactions:
                results.append(await self.reconcile_transaction(reference=transaction.reference, provider_operation=provider_operation, provider_name=provider_name))
            if len(transactions) < (page_size or self.batch_size):
                break
            page += 1
        return results

    async def reconcile_failed_transactions(
        self,
        *,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reconcile failed airtime transactions and attempt recovery where appropriate."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            transactions, _ = await self.transaction_repository.get_transactions_by_status(
                status="failed",
                page=page,
                page_size=page_size or self.batch_size,
            )
            if not transactions:
                break
            for transaction in transactions:
                results.append(await self.reconcile_transaction(reference=transaction.reference, provider_operation=provider_operation, provider_name=provider_name))
            if len(transactions) < (page_size or self.batch_size):
                break
            page += 1
        return results

    async def detect_discrepancies(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Compare internal status to provider state and identify reconciliation gaps."""
        if transaction.status in {"succeeded", "completed", "settled"}:
            metadata = self._parse_metadata(transaction.metadata_payload)
            if metadata.get("wallet_credit_applied") is not True:
                return {"type": "successful_uncredited_purchase", "message": "Provider reports success but the wallet credit was not applied."}
            return None

        if transaction.status in {"pending", "processing"}:
            provider_result = await self._query_provider(transaction=transaction, provider_operation=provider_operation, provider_name=provider_name)
            if provider_result.get("status") in {"succeeded", "completed", "settled"}:
                return {
                    "type": "successful_uncredited_purchase",
                    "message": "Provider reports a successful purchase that requires wallet crediting.",
                    "provider_status": provider_result.get("status"),
                }
            if provider_result.get("status") in {"failed", "cancelled", "reversed"}:
                return {"type": "failed_purchase", "message": provider_result.get("message") or "Provider reported failure."}
            if provider_result.get("status") == "duplicate":
                return {"type": "duplicate_purchase", "message": "Duplicate provider transaction detected."}
            return None

        if transaction.status == "failed":
            provider_result = await self._query_provider(transaction=transaction, provider_operation=provider_operation, provider_name=provider_name)
            if provider_result.get("status") in {"succeeded", "completed", "settled"}:
                return {
                    "type": "successful_uncredited_purchase",
                    "message": "Provider reports success after a previous internal failure.",
                    "provider_status": provider_result.get("status"),
                }
            if provider_result.get("status") == "duplicate":
                return {"type": "duplicate_purchase", "message": "Duplicate provider transaction detected."}
            return None

        return None

    async def reverse_failed_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Reverse a failed airtime purchase and restore the wallet balance if it was previously debited."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference(resolved.reference)
            if current is None:
                raise ValidationException("Transaction reference was not found.")
            if current.status in {"reversed", "duplicate"}:
                return await self._build_response(current, discrepancy=None)

            wallet = await self._credit_wallet_for_transaction(current)
            current.status = "reversed"
            current.metadata_payload = self._update_audit_entry(
                current.metadata_payload,
                {"event": "reversed_failed_purchase", "reason": reason or "provider_failure", "provider_name": provider_name},
            )
            current = await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            self.logger.info("airtime_purchase_reversed", extra={"reference": current.reference, "wallet_id": str(wallet.id) if wallet else None})
            return await self._build_response(current, discrepancy={"type": "failed_purchase", "message": reason or "provider_failure"})

    async def retry_failed_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed airtime purchase by re-querying the provider and updating the transaction state."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for retries.")

        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference(resolved.reference)
            if current is None:
                raise ValidationException("Transaction reference was not found.")
            current.status = "pending"
            current.metadata_payload = self._update_audit_entry(current.metadata_payload, {"event": "retry_started", "provider_name": provider_name})
            current = await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            provider_result = await self._query_provider(transaction=current, provider_operation=provider_operation, provider_name=provider_name)
            current.status = self._normalize_status(provider_result.get("status"))
            current.metadata_payload = self._update_audit_entry(current.metadata_payload, {"event": "retry_completed", "provider_status": current.status})
            current = await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            if current.status in {"succeeded", "completed", "settled"}:
                await self._credit_wallet_for_transaction(current)
            return await self._build_response(current, discrepancy=None)

    async def generate_reconciliation_report(
        self,
        *,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Create a summary report for pending, failed, succeeded, and reversed airtime transactions."""
        page_size = page_size or self.batch_size
        summary: dict[str, Any] = {"pending": 0, "failed": 0, "succeeded": 0, "reversed": 0, "duplicate": 0, "references": []}
        for status in ["pending", "failed", "succeeded", "reversed", "duplicate"]:
            page = 1
            while True:
                transactions, total = await self.transaction_repository.get_transactions_by_status(status=status, page=page, page_size=page_size)
                summary[status] += len(transactions)
                if len(transactions) < page_size:
                    break
                page += 1
                if page > 1000:
                    break
        summary["total_transactions"] = sum(summary[key] for key in ["pending", "failed", "succeeded", "reversed", "duplicate"])
        return summary

    async def _query_provider(
        self,
        *,
        transaction: Transaction,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for reconciliation.")
        self.logger.info("airtime_provider_query", extra={"reference": transaction.reference, "provider_name": provider_name})
        provider_response = await self.provider_service.execute_airtime(
            operation=provider_operation,
            validate=self._validate_provider_payload,
            normalize=self._normalize_provider_response,
            payload={
                "reference": transaction.reference,
                "amount": str(transaction.amount),
                "currency": transaction.currency,
                "wallet_id": str(transaction.wallet_id) if transaction.wallet_id else None,
                "user_id": str(transaction.user_id),
                "provider_name": provider_name,
            },
        )
        return provider_response

    async def _credit_wallet_for_transaction(self, transaction: Transaction) -> Wallet | None:
        if transaction.wallet_id is None:
            return None
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found for reconciliation crediting.")
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("wallet_credit_applied") is True:
            return wallet
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["wallet_credit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
        return wallet

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("A transaction reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Transaction reference was not found.")
        return resolved

    async def _build_response(self, transaction: Transaction, *, discrepancy: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "provider": transaction.provider_name,
            "discrepancy": discrepancy,
            "metadata": self._parse_metadata(transaction.metadata_payload),
        }

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Transaction reference is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": self._normalize_status(payload.get("status")),
            "provider": provider.name,
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
            "metadata": payload.get("metadata"),
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
            "duplicate": "duplicate",
            "pending": "pending",
            "processing": "pending",
            "in-progress": "pending",
        }
        return mapping.get(lowered, lowered)

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            loaded = json.loads(payload)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            return {"value": payload}
        return {"value": payload}

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return json.dumps(payload, default=str)

    def _update_audit_entry(self, payload: str | None, event: dict[str, Any]) -> str | None:
        metadata = self._parse_metadata(payload)
        audit_entries = metadata.get("audit")
        if not isinstance(audit_entries, list):
            audit_entries = []
        audit_entries.append(event)
        metadata["audit"] = audit_entries[-20:]
        return self._serialize_metadata(metadata)

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise


__all__ = ["AirtimeReconciliationService"]
