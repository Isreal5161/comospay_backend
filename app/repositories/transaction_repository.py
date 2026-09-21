from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.utils.exceptions import DuplicateProviderReferenceException


class TransactionRepository:
    """Repository for database access to transaction records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _is_provider_reference_unique_violation(exc: IntegrityError) -> bool:
        """Check whether an IntegrityError is specifically caused by the provider-scoped transaction constraint."""
        orig = getattr(exc, "orig", None)
        if orig is None:
            return False

        diag = getattr(orig, "diag", None)
        if diag is not None and getattr(diag, "constraint_name", None):
            return diag.constraint_name == "uq_transactions_provider_ref"

        text = str(orig).lower()
        if "uq_transactions_provider_ref" in text:
            return True
        if "transactions.provider_name, transactions.provider_reference" in text:
            return True
        if "unique constraint failed: transactions.provider_name, transactions.provider_reference" in text:
            return True
        return False

    async def create_transaction(self, transaction: Transaction) -> Transaction:
        """Create and persist a new transaction record."""
        try:
            self.session.add(transaction)
            await self.session.flush()
            await self.session.refresh(transaction)
            return transaction
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_provider_reference_unique_violation(exc):
                raise DuplicateProviderReferenceException(
                    detail=(
                        f"Duplicate provider reference '{transaction.provider_reference}' "
                        f"for provider '{transaction.provider_name}' detected."
                    )
                ) from exc
            raise

    async def get_by_id(self, transaction_id: UUID) -> Transaction | None:
        """Retrieve a transaction by primary key."""
        result = await self.session.execute(select(Transaction).where(Transaction.id == transaction_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, transaction_id: UUID) -> Transaction | None:
        """Retrieve a transaction by primary key while acquiring a row lock."""
        result = await self.session.execute(select(Transaction).where(Transaction.id == transaction_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_reference(self, reference: str) -> Transaction | None:
        """Retrieve a transaction by its unique reference."""
        result = await self.session.execute(select(Transaction).where(Transaction.reference == reference))
        return result.scalar_one_or_none()

    async def get_by_reference_for_update(self, reference: str) -> Transaction | None:
        """Retrieve a transaction by reference while acquiring a row lock."""
        result = await self.session.execute(select(Transaction).where(Transaction.reference == reference).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_provider_reference(self, *, provider_name: str | None, provider_reference: str | None) -> Transaction | None:
        """Retrieve a transaction by the provider-scoped reference used in webhook callbacks."""
        if not provider_reference or not isinstance(provider_reference, str):
            return None
        query = select(Transaction).where(Transaction.provider_reference == provider_reference)
        if provider_name is not None:
            query = query.where(Transaction.provider_name == provider_name)
        result = await self.session.execute(query)
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

        count_result = await self.session.execute(select(func.count(Transaction.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

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
        count_result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.status == status))
        total = int(count_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        transactions = list(result.scalars().all())
        return transactions, total

    async def get_admin_transactions(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        category: str | None = None,
        transaction_type: str | None = None,
        reference: str | None = None,
        user_id: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Transaction], int]:
        """Retrieve admin-facing transaction listing with filtering, sorting, and pagination."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        if page_size > 100:
            page_size = 100

        query = select(Transaction)
        count_query = select(func.count(Transaction.id))

        if status:
            query = query.where(Transaction.status == status)
            count_query = count_query.where(Transaction.status == status)
        if category:
            query = query.where(Transaction.category == category)
            count_query = count_query.where(Transaction.category == category)
        if transaction_type:
            query = query.where(Transaction.transaction_type == transaction_type)
            count_query = count_query.where(Transaction.transaction_type == transaction_type)
        if reference:
            query = query.where(Transaction.reference == reference)
            count_query = count_query.where(Transaction.reference == reference)
        if user_id:
            query = query.where(Transaction.user_id == UUID(user_id))
            count_query = count_query.where(Transaction.user_id == UUID(user_id))

        order_mapping = {
            "created_at": Transaction.created_at,
            "updated_at": Transaction.updated_at,
            "status": Transaction.status,
            "amount": Transaction.amount,
            "category": Transaction.category,
            "transaction_type": Transaction.transaction_type,
        }
        order_column = order_mapping.get(order_by, Transaction.created_at)
        if descending:
            order_column = order_column.desc()

        total_result = await self.session.execute(count_query)
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update_transaction(self, transaction: Transaction, **fields: Any) -> Transaction:
        """Update editable transaction fields in the database."""
        try:
            for field, value in fields.items():
                if hasattr(transaction, field):
                    setattr(transaction, field, value)
            self.session.add(transaction)
            await self.session.flush()
            await self.session.refresh(transaction)
            return transaction
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_provider_reference_unique_violation(exc):
                raise DuplicateProviderReferenceException(
                    detail=(
                        f"Duplicate provider reference '{transaction.provider_reference}' "
                        f"for provider '{transaction.provider_name}' detected."
                    )
                ) from exc
            raise

    async def count_user_transactions(self, user_id: UUID) -> int:
        """Count transactions for a user."""
        result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.user_id == user_id))
        return int(result.scalar_one() or 0)

    async def delete_transaction(self, transaction_id: UUID) -> None:
        """Delete a transaction record by primary key."""
        await self.session.execute(delete(Transaction).where(Transaction.id == transaction_id))
        await self.session.flush()
