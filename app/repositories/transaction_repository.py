from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction


class TransactionRepository:
    """Repository for database access to transaction records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_transaction(self, transaction: Transaction) -> Transaction:
        """Create and persist a new transaction record."""
        self.session.add(transaction)
        await self.session.flush()
        await self.session.refresh(transaction)
        return transaction

    async def get_by_id(self, transaction_id: UUID) -> Transaction | None:
        """Retrieve a transaction by primary key."""
        result = await self.session.execute(select(Transaction).where(Transaction.id == transaction_id))
        return result.scalar_one_or_none()

    async def get_by_reference(self, reference: str) -> Transaction | None:
        """Retrieve a transaction by its unique reference."""
        result = await self.session.execute(select(Transaction).where(Transaction.reference == reference))
        return result.scalar_one_or_none()

    async def get_user_transactions(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        category: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Transaction], int]:
        """Retrieve paginated transaction history for a user with optional filtering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Transaction).where(Transaction.user_id == user_id)
        if status:
            query = query.where(Transaction.status == status)
        if category:
            query = query.where(Transaction.category == category)

        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        order_column = getattr(Transaction, order_by, Transaction.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        transactions = list(result.scalars().all())
        return transactions, total

    async def get_transactions_by_status(
        self,
        *,
        status: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Transaction], int]:
        """Retrieve transactions filtered by lifecycle status."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Transaction).where(Transaction.status == status)
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        result = await self.session.execute(
            query.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        transactions = list(result.scalars().all())
        return transactions, total

    async def update_transaction(self, transaction: Transaction, **fields: Any) -> Transaction:
        """Update editable transaction fields in the database."""
        for field, value in fields.items():
            if hasattr(transaction, field):
                setattr(transaction, field, value)
        self.session.add(transaction)
        await self.session.flush()
        await self.session.refresh(transaction)
        return transaction

    async def count_user_transactions(self, user_id: UUID) -> int:
        """Count transactions for a user."""
        result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.user_id == user_id))
        return int(result.scalar_one() or 0)

    async def delete_transaction(self, transaction_id: UUID) -> None:
        """Delete a transaction record by primary key."""
        await self.session.execute(delete(Transaction).where(Transaction.id == transaction_id))
        await self.session.flush()
