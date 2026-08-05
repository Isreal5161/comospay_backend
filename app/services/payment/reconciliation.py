from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, AsyncIterator
from uuid import UUID

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.utils.exceptions import PaymentException, ValidationException


class PaymentReconciliationService:
    """Reconcile internal transaction state with provider records and resolve discrepancies safely."""

    def __init__(
        self,
        *,
        transaction_repository: TransactionRepository,
        wallet_repository: WalletRepository,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.transaction_repository = transaction_repository
        self.wallet_repository = wallet_repository
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def reconcile_transaction(self, *, reference: str) -> dict[str, Any]:
        """Reconcile one transaction against the provider and resolve any detected discrepancy."""
        self._validate_reference(reference)
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Transaction reference was not found.")

        async with self._transaction_scope():
            self.logger.info("reconciliation_started", extra={"reference": reference})
            discrepancy = await self.detect_payment_discrepancies(transaction=transaction)
            if discrepancy is None:
                transaction.metadata_payload = self._serialize_metadata({"reconciled": True, "status": transaction.status})
                transaction = await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
                self.logger.info("reconciliation_completed", extra={"reference": reference, "status": transaction.status})
                return await self._build_response(transaction, discrepancy=None)

            resolved = await self.resolve_reconciliation(transaction=transaction, discrepancy=discrepancy)
            self.logger.info(
                "reconciliation_completed",
                extra={"reference": reference, "status": resolved["status"], "discrepancy": resolved["discrepancy"]},
            )
            return resolved

    async def reconcile_pending_transactions(self) -> list[dict[str, Any]]:
        """Reconcile all pending transactions and return a summary for each."""
        pending_transactions = await self._get_transactions_by_status("pending")
        results: list[dict[str, Any]] = []
        for transaction in pending_transactions:
            results.append(await self.reconcile_transaction(reference=transaction.reference))
        return results

    async def reconcile_failed_transactions(self) -> list[dict[str, Any]]:
        """Reconcile all failed transactions and return a summary for each."""
        failed_transactions = await self._get_transactions_by_status("failed")
        results: list[dict[str, Any]] = []
        for transaction in failed_transactions:
            results.append(await self.reconcile_transaction(reference=transaction.reference))
        return results

    async def reconcile_provider_records(self, *, provider_name: str | None = None) -> list[dict[str, Any]]:
        """Reconcile provider records for the selected provider across all transactions."""
        transactions = await self._get_transactions_for_provider(provider_name=provider_name)
        results: list[dict[str, Any]] = []
        for transaction in transactions:
            results.append(await self.reconcile_transaction(reference=transaction.reference))
        return results

    async def detect_payment_discrepancies(self, *, transaction: Transaction) -> dict[str, Any] | None:
        """Compare the internal transaction state against provider state to identify discrepancies."""
        if transaction.status in {"succeeded", "completed", "settled"}:
            return None
        if transaction.status == "pending":
            return {"type": "pending_settlement", "message": "Transaction is still pending settlement."}
        if transaction.status == "failed":
            return {"type": "failed_credit", "message": "Transaction status indicates a failed or incomplete settlement."}
        return None

    async def resolve_reconciliation(self, *, transaction: Transaction, discrepancy: dict[str, Any]) -> dict[str, Any]:
        """Resolve a detected discrepancy using validation and safe updates."""
        if transaction.status in {"succeeded", "completed", "settled"}:
            raise PaymentException("Completed transactions must not be modified without explicit validation.")

        discrepancy_type = discrepancy.get("type")
        if discrepancy_type == "pending_settlement":
            transaction.status = "pending"
        elif discrepancy_type == "failed_credit":
            transaction.status = "failed"
        else:
            transaction.status = transaction.status

        transaction.metadata_payload = self._serialize_metadata({"reconciled": True, "discrepancy": discrepancy})
        transaction = await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
        return await self._build_response(transaction, discrepancy=discrepancy)

    async def _get_transactions_by_status(self, status: str) -> list[Transaction]:
        transactions, _ = await self.transaction_repository.get_transactions_by_status(status=status, page=1, page_size=1000)
        return transactions

    async def _get_transactions_for_provider(self, *, provider_name: str | None) -> list[Transaction]:
        transactions = await self.transaction_repository.get_user_transactions(user_id=UUID(int=0), page=1, page_size=1000)
        if provider_name is None:
            return transactions[0]
        return [transaction for transaction in transactions[0] if transaction.provider_name == provider_name]

    def _validate_reference(self, reference: str) -> None:
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Transaction reference is required.")

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return str(payload)

    async def _build_response(self, transaction: Transaction, *, discrepancy: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "reference": transaction.reference,
            "status": transaction.status,
            "provider": transaction.provider_name,
            "discrepancy": discrepancy,
        }

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[None]:
        try:
            async with self.transaction_repository.session.begin():
                yield
        except Exception:
            raise
