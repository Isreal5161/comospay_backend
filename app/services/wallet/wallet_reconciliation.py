from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ValidationException, WalletException


class WalletReconciliationService:
    """Reconcile wallet balances, transactions, and provider records for consistency."""

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def reconcile_wallet(self, *, wallet_id: UUID, dry_run: bool = True) -> dict[str, Any]:
        """Compare wallet balances and transaction history for a single wallet."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")

        report = await self.generate_reconciliation_report(wallet_id=wallet.id, dry_run=dry_run)
        await self._log_event("wallet_reconciliation_completed", user_id=wallet.user_id, metadata={"wallet_id": str(wallet.id), "dry_run": dry_run})
        return report

    async def reconcile_all_wallets(self, *, dry_run: bool = True) -> dict[str, Any]:
        """Reconcile all known wallets in the current store."""
        self._require_repository(self.wallet_repository)
        wallets = await self._get_all_wallets()

        reports: list[dict[str, Any]] = []
        for wallet in wallets:
            reports.append(await self.generate_reconciliation_report(wallet_id=wallet.id, dry_run=dry_run))

        inconsistent_wallets = [report for report in reports if not report["consistency"]["is_consistent"]]
        return {
            "dry_run": dry_run,
            "wallet_count": len(reports),
            "inconsistent_wallet_count": len(inconsistent_wallets),
            "wallets": reports,
        }

    async def verify_transaction_integrity(self, *, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Validate that wallet-linked transactions are structurally consistent."""
        transactions = await self._get_transactions(wallet_id=wallet_id)
        findings: list[dict[str, Any]] = []
        for transaction in transactions:
            if transaction.wallet_id is None:
                findings.append({"type": "missing_wallet_reference", "transaction_id": str(transaction.id), "reference": transaction.reference})
            if transaction.amount < Decimal("0.00"):
                findings.append({"type": "negative_amount", "transaction_id": str(transaction.id), "reference": transaction.reference})
            if transaction.total_amount < Decimal("0.00"):
                findings.append({"type": "negative_total_amount", "transaction_id": str(transaction.id), "reference": transaction.reference})
            if transaction.status not in {"pending", "completed", "failed", "reversed", "posted"}:
                findings.append({"type": "unknown_status", "transaction_id": str(transaction.id), "reference": transaction.reference, "status": transaction.status})
            if transaction.total_amount < transaction.amount:
                findings.append({"type": "inconsistent_total_amount", "transaction_id": str(transaction.id), "reference": transaction.reference})

        return {
            "wallet_id": str(wallet_id) if wallet_id is not None else None,
            "transaction_count": len(transactions),
            "integrity_issues": findings,
            "is_consistent": not findings,
        }

    async def detect_duplicate_transactions(self, *, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Find duplicate transactions based on reference values."""
        transactions = await self._get_transactions(wallet_id=wallet_id)
        reference_map: dict[str, list[Transaction]] = defaultdict(list)
        for transaction in transactions:
            reference_map[transaction.reference].append(transaction)

        duplicates: list[dict[str, Any]] = []
        for reference, grouped in reference_map.items():
            if len(grouped) > 1:
                duplicates.append(
                    {
                        "reference": reference,
                        "transaction_ids": [str(item.id) for item in grouped],
                    }
                )

        return {
            "wallet_id": str(wallet_id) if wallet_id is not None else None,
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        }

    async def detect_missing_transactions(self, *, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Identify possible missing transactions by comparing wallet balances to derived transaction totals."""
        wallet = await self._get_wallet(wallet_id=wallet_id) if wallet_id is not None else None
        transactions = await self._get_transactions(wallet_id=wallet_id)
        expected = await self.calculate_expected_balance(wallet_id=wallet_id) if wallet_id is not None else {"expected_balance": Decimal("0.00")}

        issues: list[dict[str, Any]] = []
        if wallet is not None and wallet.ledger_balance != Decimal("0.00") and not transactions:
            issues.append({"type": "wallet_has_balance_without_transactions", "wallet_id": str(wallet.id)})
        if wallet is not None and wallet.ledger_balance != self._normalize_amount(expected["expected_balance"]):
            issues.append(
                {
                    "type": "balance_mismatch",
                    "wallet_id": str(wallet.id),
                    "stored_ledger_balance": str(wallet.ledger_balance),
                    "expected_balance": str(expected["expected_balance"]),
                }
            )

        return {
            "wallet_id": str(wallet_id) if wallet_id is not None else None,
            "missing_transaction_count": len(issues),
            "issues": issues,
        }

    async def calculate_expected_balance(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Calculate the balance implied by transaction history for a wallet."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")

        transactions = await self._get_transactions(wallet_id=wallet.id)
        expected_balance = Decimal("0.00")
        for transaction in transactions:
            if transaction.status.lower() not in {"completed", "posted", "successful", "succeeded"}:
                continue
            expected_balance += self._transaction_effect(transaction)

        return {
            "wallet_id": str(wallet.id),
            "expected_balance": str(expected_balance),
            "transaction_count": len(transactions),
        }

    async def rebuild_wallet_balance(self, *, wallet_id: UUID, dry_run: bool = True) -> dict[str, Any]:
        """Recalculate wallet balances from transaction history and optionally persist them."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")

        expected = await self.calculate_expected_balance(wallet_id=wallet.id)
        expected_balance = self._normalize_amount(expected["expected_balance"])
        if dry_run:
            return {
                "wallet_id": str(wallet.id),
                "dry_run": True,
                "current_available_balance": str(wallet.available_balance),
                "current_ledger_balance": str(wallet.ledger_balance),
                "projected_available_balance": str(expected_balance),
                "projected_ledger_balance": str(expected_balance),
                "would_update": wallet.available_balance != expected_balance or wallet.ledger_balance != expected_balance,
            }

        try:
            async with self._session_scope():
                wallet_for_update = await self._load_wallet_for_update(wallet_id)
                if wallet_for_update is None:
                    raise ValidationException("Wallet not found.")
                wallet_for_update.available_balance = expected_balance
                wallet_for_update.ledger_balance = expected_balance
                await self.wallet_repository.update_balance_fields(
                    wallet_for_update,
                    available_balance=expected_balance,
                    ledger_balance=expected_balance,
                )
            return {
                "wallet_id": str(wallet.id),
                "dry_run": False,
                "current_available_balance": str(wallet.available_balance),
                "current_ledger_balance": str(wallet.ledger_balance),
                "projected_available_balance": str(expected_balance),
                "projected_ledger_balance": str(expected_balance),
                "would_update": True,
            }
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Wallet balance rebuild failed.") from exc

    async def generate_reconciliation_report(self, *, wallet_id: UUID | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Generate a comprehensive reconciliation report for one or all wallets."""
        if wallet_id is not None:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
            if wallet is None:
                raise ValidationException("Wallet not found.")
            consistency = await self.validate_wallet_consistency(wallet_id=wallet.id)
            duplicates = await self.detect_duplicate_transactions(wallet_id=wallet.id)
            missing = await self.detect_missing_transactions(wallet_id=wallet.id)
            integrity = await self.verify_transaction_integrity(wallet_id=wallet.id)
            provider = await self.reconcile_provider_records(wallet_id=wallet.id)
            return {
                "wallet_id": str(wallet.id),
                "dry_run": dry_run,
                "consistency": consistency,
                "duplicates": duplicates,
                "missing_transactions": missing,
                "transaction_integrity": integrity,
                "provider_records": provider,
            }

        wallets = await self._get_all_wallets()
        reports: list[dict[str, Any]] = []
        for wallet in wallets:
            reports.append(await self.generate_reconciliation_report(wallet_id=wallet.id, dry_run=dry_run))
        return {"wallet_count": len(reports), "wallets": reports}

    async def validate_wallet_consistency(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Validate that stored balances match the derived transaction-based balance."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")

        expected = await self.calculate_expected_balance(wallet_id=wallet.id)
        expected_balance = self._normalize_amount(expected["expected_balance"])
        issues: list[dict[str, Any]] = []
        if wallet.available_balance != expected_balance:
            issues.append({"type": "available_balance_mismatch", "expected": str(expected_balance), "actual": str(wallet.available_balance)})
        if wallet.ledger_balance != expected_balance:
            issues.append({"type": "ledger_balance_mismatch", "expected": str(expected_balance), "actual": str(wallet.ledger_balance)})

        return {
            "wallet_id": str(wallet.id),
            "is_consistent": not issues,
            "expected_balance": str(expected_balance),
            "available_balance": str(wallet.available_balance),
            "ledger_balance": str(wallet.ledger_balance),
            "issues": issues,
        }

    async def reconcile_provider_records(self, *, wallet_id: UUID | None = None) -> dict[str, Any]:
        """Compare provider-linked records with internal transactions and surface mismatches."""
        transactions = await self._get_transactions(wallet_id=wallet_id)
        mismatches: list[dict[str, Any]] = []
        for transaction in transactions:
            provider_present = bool(transaction.provider_name or transaction.provider_reference)
            if transaction.status in {"completed", "posted"} and not provider_present:
                mismatches.append({"type": "missing_provider_reference", "transaction_id": str(transaction.id), "reference": transaction.reference})
            if transaction.provider_name and not transaction.provider_reference:
                mismatches.append({"type": "missing_provider_reference_value", "transaction_id": str(transaction.id), "reference": transaction.reference})
            if transaction.provider_reference and not transaction.provider_name:
                mismatches.append({"type": "missing_provider_name", "transaction_id": str(transaction.id), "reference": transaction.reference})

        return {
            "wallet_id": str(wallet_id) if wallet_id is not None else None,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }

    async def _get_all_wallets(self) -> list[Wallet]:
        if self.session is None:
            result = await self.wallet_repository.session.execute(select(Wallet))
            return list(result.scalars().all())
        result = await self.session.execute(select(Wallet))
        return list(result.scalars().all())

    async def _get_transactions(self, *, wallet_id: UUID | None = None) -> list[Transaction]:
        if self.session is None:
            query = select(Transaction)
            if wallet_id is not None:
                query = query.where(Transaction.wallet_id == wallet_id)
            result = await self.transaction_repository.session.execute(query.order_by(Transaction.created_at.asc()))
            return list(result.scalars().all())

        query = select(Transaction)
        if wallet_id is not None:
            query = query.where(Transaction.wallet_id == wallet_id)
        result = await self.session.execute(query.order_by(Transaction.created_at.asc()))
        return list(result.scalars().all())

    async def _get_wallet(self, *, wallet_id: UUID | None) -> Wallet | None:
        if wallet_id is None:
            return None
        return await self.wallet_repository.get_by_id(wallet_id)

    async def _load_wallet_for_update(self, wallet_id: UUID) -> Wallet | None:
        if self.session is None:
            return await self.wallet_repository.get_by_id_for_update(wallet_id)
        stmt = select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "wallet_reconciliation_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )

    def _transaction_effect(self, transaction: Transaction) -> Decimal:
        normalized_status = (transaction.status or "").strip().lower()
        if normalized_status in {"reversed", "failed"}:
            return Decimal("0.00")

        lowered_type = f"{transaction.transaction_type} {transaction.category}".lower()
        if "fund" in lowered_type or "credit" in lowered_type or "deposit" in lowered_type:
            return self._normalize_amount(transaction.amount)
        if "withdraw" in lowered_type or "transfer" in lowered_type or "debit" in lowered_type:
            return -self._normalize_amount(transaction.amount)
        return self._normalize_amount(transaction.amount)

    def _normalize_amount(self, amount: Decimal | float | int | str) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletReconciliationService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
