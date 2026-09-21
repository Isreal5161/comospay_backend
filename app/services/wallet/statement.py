from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ValidationException, WalletException


class WalletStatementService:
    """Provide wallet statement generation and transaction history services."""

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service

    async def get_wallet_statement(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Return a wallet statement payload with filtered transaction history."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            await self._validate_pagination(page=page, page_size=page_size)
            start_dt, end_dt = self._normalize_date_range(start_date=start_date, end_date=end_date)

            filters = self._build_filters(status=status, transaction_type=transaction_type, start_date=start_dt, end_date=end_dt)
            transactions = await self._load_transactions(
                user_id=user_id,
                wallet_id=wallet.id,
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_desc=sort_desc,
                **filters,
            )
            summary = await self.calculate_statement_summary(
                user_id=user_id,
                wallet_id=wallet.id,
                start_date=start_date,
                end_date=end_date,
                status=status,
                transaction_type=transaction_type,
            )

            await self._log_event("wallet_statement_requested", user_id=user_id, metadata={"wallet_id": str(wallet.id), "page": page})
            return {
                "wallet_id": str(wallet.id),
                "user_id": str(wallet.user_id),
                "currency": wallet.currency,
                "statement": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "filters": filters,
                    "page": page,
                    "page_size": page_size,
                    "sort_by": sort_by,
                    "sort_desc": sort_desc,
                    "transactions": [self._serialize_transaction(tx) for tx in transactions["items"]],
                    "summary": summary,
                },
            }
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Wallet statement retrieval failed.") from exc

    async def get_transaction_history(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Return the recent transaction history for a wallet."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            await self._validate_pagination(page=page, page_size=page_size)
            start_dt, end_dt = self._normalize_date_range(start_date=start_date, end_date=end_date)
            filters = self._build_filters(status=status, transaction_type=transaction_type, start_date=start_dt, end_date=end_dt)
            transactions = await self._load_transactions(
                user_id=user_id,
                wallet_id=wallet.id,
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_desc=sort_desc,
                **filters,
            )
            return {
                "wallet_id": str(wallet.id),
                "transactions": [self._serialize_transaction(tx) for tx in transactions["items"]],
                "page": page,
                "page_size": page_size,
                "total": transactions["total"],
                "has_next": transactions["has_next"],
            }
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Transaction history retrieval failed.") from exc

    async def get_transaction_details(self, *, user_id: UUID, transaction_id: UUID) -> dict[str, Any]:
        """Return a single transaction with ownership validation."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            transaction = await self.transaction_repository.get_by_id(transaction_id)
            if not transaction:
                raise ValidationException("Transaction not found.")
            if transaction.wallet_id is None:
                raise ValidationException("Transaction is not linked to a wallet.")
            wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
            if wallet is None or wallet.user_id != user_id:
                raise WalletException("You do not have access to this transaction.")
            return self._serialize_transaction(transaction)
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Transaction details retrieval failed.") from exc

    async def search_transactions(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        query: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Search transactions by reference, description, or provider reference."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            await self._validate_pagination(page=page, page_size=page_size)
            if not query or not query.strip():
                raise ValidationException("Search query is required.")

            needle = query.strip().lower()
            if self.session is None:
                transactions = await self.transaction_repository.get_user_transactions(user_id=user_id, page=1, page_size=1000)
                items = [tx for tx in transactions[0] if tx.wallet_id == wallet.id and self._matches_query(tx, needle)]
                total = len(items)
                slice_start = (page - 1) * page_size
                slice_end = slice_start + page_size
                paged_items = items[slice_start:slice_end]
            else:
                query_stmt = (
                    select(Transaction)
                    .where(Transaction.user_id == user_id)
                    .where(Transaction.wallet_id == wallet.id)
                    .where(
                        (Transaction.reference.ilike(f"%{needle}%"))
                        | (Transaction.description.ilike(f"%{needle}%"))
                        | (Transaction.provider_reference.ilike(f"%{needle}%"))
                    )
                )
                result = await self.session.execute(query_stmt.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
                paged_items = list(result.scalars().all())
                count_result = await self.session.execute(
                    select(func.count(Transaction.id)).where(Transaction.user_id == user_id).where(Transaction.wallet_id == wallet.id)
                )
                total = int(count_result.scalar_one() or 0)

            return {
                "wallet_id": str(wallet.id),
                "query": query.strip(),
                "transactions": [self._serialize_transaction(tx) for tx in paged_items],
                "page": page,
                "page_size": page_size,
                "total": total,
                "has_next": page * page_size < total,
            }
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Transaction search failed.") from exc

    async def export_statement(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        format: str = "csv",
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        """Export wallet transactions using the existing statement service patterns."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        normalized_format = (format or "csv").lower()
        if normalized_format == "pdf":
            return await self.export_statement_pdf(
                user_id=user_id,
                wallet_id=wallet_id,
                start_date=start_date,
                end_date=end_date,
                status=status,
                transaction_type=transaction_type,
            )
        if normalized_format == "csv":
            return await self.export_statement_csv(
                user_id=user_id,
                wallet_id=wallet_id,
                start_date=start_date,
                end_date=end_date,
                status=status,
                transaction_type=transaction_type,
            )

        raise ValidationException("Unsupported export format. Use 'csv' or 'pdf'.")

    async def export_statement_pdf(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        """Export a statement payload suitable for PDF rendering."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            start_dt, end_dt = self._normalize_date_range(start_date=start_date, end_date=end_date)
            filters = self._build_filters(status=status, transaction_type=transaction_type, start_date=start_dt, end_date=end_dt)
            transactions = await self._load_transactions(user_id=user_id, wallet_id=wallet.id, page=1, page_size=1000, **filters)
            lines = [f"Wallet Statement for {wallet.id}", f"Currency: {wallet.currency}"]
            for tx in transactions["items"]:
                lines.append(f"- {tx.reference} | {tx.status} | {tx.amount} | {tx.created_at.isoformat() if tx.created_at else '-'}")
            return {"wallet_id": str(wallet.id), "format": "pdf", "payload": "\n".join(lines), "content_type": "application/pdf"}
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("PDF statement export failed.") from exc

    async def export_statement_csv(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        """Export a statement payload as CSV text."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            start_dt, end_dt = self._normalize_date_range(start_date=start_date, end_date=end_date)
            filters = self._build_filters(status=status, transaction_type=transaction_type, start_date=start_dt, end_date=end_dt)
            transactions = await self._load_transactions(user_id=user_id, wallet_id=wallet.id, page=1, page_size=1000, **filters)
            csv_buffer = StringIO()
            writer = csv.DictWriter(csv_buffer, fieldnames=["id", "reference", "status", "amount", "currency", "created_at"])
            writer.writeheader()
            for tx in transactions["items"]:
                row = self._serialize_transaction(tx)
                writer.writerow({key: row[key] for key in ["id", "reference", "status", "amount", "currency", "created_at"]})
            return {"wallet_id": str(wallet.id), "format": "csv", "payload": csv_buffer.getvalue(), "content_type": "text/csv"}
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("CSV statement export failed.") from exc

    async def calculate_statement_summary(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        """Generate high-level transaction statistics for a wallet statement."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            wallet = await self._get_wallet_with_ownership_check(wallet_id=wallet_id, user_id=user_id)
            start_dt, end_dt = self._normalize_date_range(start_date=start_date, end_date=end_date)
            filters = self._build_filters(status=status, transaction_type=transaction_type, start_date=start_dt, end_date=end_dt)
            transactions = await self._load_transactions(user_id=user_id, wallet_id=wallet.id, page=1, page_size=1000, **filters)
            items = transactions["items"]
            total_credits = sum((tx.amount for tx in items if self._is_credit_transaction(tx)), Decimal("0.00"))
            total_debits = sum((tx.amount for tx in items if self._is_debit_transaction(tx)), Decimal("0.00"))
            total_fees = sum((tx.charges for tx in items if tx.charges is not None), Decimal("0.00"))
            net_flow = total_credits - total_debits
            opening_balance = wallet.available_balance - net_flow
            closing_balance = wallet.available_balance

            return {
                "wallet_id": str(wallet.id),
                "currency": wallet.currency,
                "period": {"start_date": start_date, "end_date": end_date},
                "total_credits": str(total_credits.quantize(Decimal("0.01"))),
                "total_debits": str(total_debits.quantize(Decimal("0.01"))),
                "total_fees": str(total_fees.quantize(Decimal("0.01"))),
                "opening_balance": str(opening_balance.quantize(Decimal("0.01"))),
                "closing_balance": str(closing_balance.quantize(Decimal("0.01"))),
                "transaction_count": len(items),
            }
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Statement summary calculation failed.") from exc

    async def _load_transactions(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        page: int,
        page_size: int,
        sort_by: str = "created_at",
        sort_desc: bool = True,
        **filters: Any,
    ) -> dict[str, Any]:
        try:
            if self.session is None:
                transactions = await self.transaction_repository.get_user_transactions(user_id=user_id, page=1, page_size=1000)
                items = [tx for tx in transactions[0] if tx.wallet_id == wallet_id and self._matches_filters(tx, filters)]
                total = len(items)
                ordered = sorted(items, key=lambda tx: getattr(tx, sort_by, tx.created_at) or tx.created_at, reverse=sort_desc)
                paged_items = ordered[(page - 1) * page_size : page * page_size]
            else:
                stmt = select(Transaction).where(Transaction.wallet_id == wallet_id)
                if filters.get("status"):
                    stmt = stmt.where(Transaction.status == filters["status"])
                if filters.get("transaction_type"):
                    stmt = stmt.where(Transaction.transaction_type == filters["transaction_type"])
                if filters.get("start_date"):
                    stmt = stmt.where(Transaction.created_at >= filters["start_date"])
                if filters.get("end_date"):
                    stmt = stmt.where(Transaction.created_at <= filters["end_date"])
                order_column = getattr(Transaction, sort_by, Transaction.created_at)
                if sort_desc:
                    order_column = order_column.desc()
                result = await self.session.execute(stmt.order_by(order_column).offset((page - 1) * page_size).limit(page_size))
                paged_items = list(result.scalars().all())
                count_result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.wallet_id == wallet_id))
                total = int(count_result.scalar_one() or 0)

            return {
                "items": paged_items,
                "total": total,
                "has_next": page * page_size < total,
            }
        except Exception as exc:
            raise DatabaseException("Transaction loading failed.") from exc

    def _build_filters(self, *, status: str | None = None, transaction_type: str | None = None, start_date: datetime | None = None, end_date: datetime | None = None) -> dict[str, Any]:
        return {
            "status": status,
            "transaction_type": transaction_type,
            "start_date": start_date,
            "end_date": end_date,
        }

    def _normalize_date_range(self, *, start_date: str | None = None, end_date: str | None = None) -> tuple[datetime | None, datetime | None]:
        def parse(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationException("Invalid date format. Use ISO 8601.") from exc
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        start_dt = parse(start_date)
        end_dt = parse(end_date)
        if start_dt and end_dt and start_dt > end_dt:
            raise ValidationException("Start date must be before or equal to the end date.")
        return start_dt, end_dt

    async def _validate_pagination(self, *, page: int, page_size: int) -> None:
        if page < 1:
            raise ValidationException("Page must be greater than or equal to 1.")
        if page_size < 1 or page_size > 100:
            raise ValidationException("Page size must be between 1 and 100.")

    async def _get_wallet_with_ownership_check(self, *, wallet_id: UUID, user_id: UUID) -> Wallet:
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException("Wallet not found.")
        if wallet.user_id != user_id:
            raise WalletException("You do not have access to this wallet.")
        return wallet

    def _matches_query(self, transaction: Transaction, needle: str) -> bool:
        values = [transaction.reference, transaction.description, transaction.provider_reference]
        return any(value and needle in value.lower() for value in values)

    def _matches_filters(self, transaction: Transaction, filters: dict[str, Any]) -> bool:
        if filters.get("status") and transaction.status != filters["status"]:
            return False
        if filters.get("transaction_type") and transaction.transaction_type != filters["transaction_type"]:
            return False
        if filters.get("start_date") and transaction.created_at and transaction.created_at < filters["start_date"]:
            return False
        if filters.get("end_date") and transaction.created_at and transaction.created_at > filters["end_date"]:
            return False
        return True

    def _is_credit_transaction(self, transaction: Transaction) -> bool:
        lowered_type = (transaction.transaction_type or "").lower()
        if "fund" in lowered_type or "credit" in lowered_type or "refund" in lowered_type:
            return True
        return transaction.amount > 0 and "transfer" not in lowered_type and "withdraw" not in lowered_type and "payment" not in lowered_type

    def _is_debit_transaction(self, transaction: Transaction) -> bool:
        lowered_type = (transaction.transaction_type or "").lower()
        if "withdraw" in lowered_type or "transfer" in lowered_type or "payment" in lowered_type or "debit" in lowered_type:
            return True
        return transaction.amount < 0

    def _serialize_transaction(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "description": transaction.description,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
        }

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "wallet_statement_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if self.audit_service is not None:
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletStatementService.")
