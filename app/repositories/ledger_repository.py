from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ledger import Ledger
from app.utils.exceptions import DatabaseException, ValidationException

logger = logging.getLogger(__name__)


class LedgerRepository:
    """Repository for database access to ledger records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, ledger: Ledger) -> Ledger:
        """Create and persist a new ledger record."""
        try:
            self.session.add(ledger)
            await self.session.flush()
            await self.session.refresh(ledger)
            return ledger
        except IntegrityError as exc:
            raise ValidationException(detail="Ledger creation failed due to invalid data.") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to create ledger record")
            raise DatabaseException(detail="Unable to create ledger record.") from exc

    async def bulk_create(self, ledgers: list[Ledger]) -> list[Ledger]:
        """Create multiple ledger records in a single flush."""
        if not ledgers:
            return []

        try:
            self.session.add_all(ledgers)
            await self.session.flush()
            for ledger in ledgers:
                await self.session.refresh(ledger)
            return ledgers
        except IntegrityError as exc:
            raise ValidationException(detail="Ledger bulk creation failed due to invalid data.") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to bulk create ledger records")
            raise DatabaseException(detail="Unable to create ledger records.") from exc

    async def get_by_id(self, ledger_id: UUID) -> Ledger | None:
        """Retrieve a ledger record by primary key."""
        try:
            result = await self.session.execute(select(Ledger).where(Ledger.ledger_id == ledger_id))
            return result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger by id")
            raise DatabaseException(detail="Unable to fetch ledger record.") from exc

    async def get_by_reference(self, transaction_reference: str) -> Ledger | None:
        """Retrieve a ledger entry by transaction reference."""
        try:
            result = await self.session.execute(
                select(Ledger).where(Ledger.transaction_reference == transaction_reference)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger by reference")
            raise DatabaseException(detail="Unable to fetch ledger record.") from exc

    async def get_by_transaction_id(self, related_transaction_id: UUID) -> list[Ledger]:
        """Retrieve ledger entries associated with a transaction."""
        try:
            result = await self.session.execute(
                select(Ledger).where(Ledger.related_transaction_id == related_transaction_id)
            )
            return list(result.scalars().all())
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger by transaction id")
            raise DatabaseException(detail="Unable to fetch ledger records.") from exc

    async def get_by_wallet_id(self, wallet_id: UUID) -> list[Ledger]:
        """Retrieve ledger entries for a wallet."""
        try:
            result = await self.session.execute(select(Ledger).where(Ledger.wallet_id == wallet_id))
            return list(result.scalars().all())
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger by wallet id")
            raise DatabaseException(detail="Unable to fetch ledger records.") from exc

    async def get_by_user_id(self, user_id: UUID) -> list[Ledger]:
        """Retrieve ledger entries for a user."""
        try:
            result = await self.session.execute(select(Ledger).where(Ledger.user_id == user_id))
            return list(result.scalars().all())
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger by user id")
            raise DatabaseException(detail="Unable to fetch ledger records.") from exc

    async def get_wallet_ledger(self, *, wallet_id: UUID, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve paginated ledger entries for a wallet."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        try:
            query = select(Ledger).where(Ledger.wallet_id == wallet_id)
            count_result = await self.session.execute(query)
            total = len(count_result.scalars().all())

            result = await self.session.execute(
                query.order_by(Ledger.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
            return list(result.scalars().all()), total
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch wallet ledger")
            raise DatabaseException(detail="Unable to fetch wallet ledger.") from exc

    async def get_user_ledger(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve paginated ledger entries for a user."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        try:
            query = select(Ledger).where(Ledger.user_id == user_id)
            count_result = await self.session.execute(query)
            total = len(count_result.scalars().all())

            result = await self.session.execute(
                query.order_by(Ledger.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
            return list(result.scalars().all()), total
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch user ledger")
            raise DatabaseException(detail="Unable to fetch user ledger.") from exc

    async def search(
        self,
        *,
        query: str | None = None,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        transaction_reference: str | None = None,
        transaction_type: str | None = None,
        entry_type: str | None = None,
        currency: str | None = None,
        status: str | None = None,
        created_by: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ledger], int]:
        """Search ledger records using optional filters and a free-text query."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        try:
            stmt = select(Ledger)
            if query:
                stmt = stmt.where(
                    (Ledger.transaction_reference.ilike(f"%{query}%"))
                    | (Ledger.description.ilike(f"%{query}%"))
                )
            if wallet_id:
                stmt = stmt.where(Ledger.wallet_id == wallet_id)
            if user_id:
                stmt = stmt.where(Ledger.user_id == user_id)
            if transaction_reference:
                stmt = stmt.where(Ledger.transaction_reference == transaction_reference)
            if transaction_type:
                stmt = stmt.where(Ledger.transaction_type == transaction_type)
            if entry_type:
                stmt = stmt.where(Ledger.entry_type == entry_type)
            if currency:
                stmt = stmt.where(Ledger.currency == currency.upper())
            if status:
                stmt = stmt.where(Ledger.status == status)
            if created_by:
                stmt = stmt.where(Ledger.created_by == created_by)
            if start_date:
                stmt = stmt.where(Ledger.created_at >= start_date)
            if end_date:
                stmt = stmt.where(Ledger.created_at <= end_date)

            count_result = await self.session.execute(stmt)
            total = len(count_result.scalars().all())

            result = await self.session.execute(
                stmt.order_by(Ledger.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            )
            return list(result.scalars().all()), total
        except SQLAlchemyError as exc:
            logger.exception("Failed to search ledger records")
            raise DatabaseException(detail="Unable to search ledger records.") from exc

    async def filter(
        self,
        *,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        related_transaction_id: UUID | None = None,
        transaction_reference: str | None = None,
        transaction_type: str | None = None,
        entry_type: str | None = None,
        currency: str | None = None,
        status: str | None = None,
        created_by: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ledger], int]:
        """Filter ledger records by supported fields."""
        return await self.search(
            query=None,
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type,
            entry_type=entry_type,
            currency=currency,
            status=status,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )

    async def list(self, *, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve a paginated list of ledger records."""
        return await self.filter(page=page, page_size=page_size)

    async def paginate(self, *, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Alias for paginated listing of ledger records."""
        return await self.list(page=page, page_size=page_size)

    async def update(self, ledger: Ledger, **fields: Any) -> Ledger:
        """Update editable ledger fields in the database."""
        try:
            for field, value in fields.items():
                if hasattr(ledger, field):
                    setattr(ledger, field, value)
            self.session.add(ledger)
            await self.session.flush()
            await self.session.refresh(ledger)
            return ledger
        except IntegrityError as exc:
            raise ValidationException(detail="Ledger update failed due to invalid data.") from exc
        except SQLAlchemyError as exc:
            logger.exception("Failed to update ledger record")
            raise DatabaseException(detail="Unable to update ledger record.") from exc

    async def delete(self, ledger_id: UUID) -> None:
        """Delete a ledger record by primary key."""
        try:
            await self.session.execute(delete(Ledger).where(Ledger.ledger_id == ledger_id))
            await self.session.flush()
        except SQLAlchemyError as exc:
            logger.exception("Failed to delete ledger record")
            raise DatabaseException(detail="Unable to delete ledger record.") from exc

    async def archive(self, ledger_id: UUID) -> Ledger | None:
        """Archive a ledger record by updating its status to archived."""
        try:
            ledger = await self.get_by_id(ledger_id)
            if ledger is None:
                return None
            ledger.status = "ARCHIVED"
            self.session.add(ledger)
            await self.session.flush()
            await self.session.refresh(ledger)
            return ledger
        except SQLAlchemyError as exc:
            logger.exception("Failed to archive ledger record")
            raise DatabaseException(detail="Unable to archive ledger record.") from exc

    async def exists(self, ledger_id: UUID) -> bool:
        """Check whether a ledger record exists."""
        try:
            result = await self.session.execute(select(func.count(Ledger.ledger_id)).where(Ledger.ledger_id == ledger_id))
            return int(result.scalar_one() or 0) > 0
        except SQLAlchemyError as exc:
            logger.exception("Failed to check ledger existence")
            raise DatabaseException(detail="Unable to verify ledger existence.") from exc

    async def count(self) -> int:
        """Count all ledger records."""
        try:
            result = await self.session.execute(select(func.count(Ledger.ledger_id)))
            return int(result.scalar_one() or 0)
        except SQLAlchemyError as exc:
            logger.exception("Failed to count ledger records")
            raise DatabaseException(detail="Unable to count ledger records.") from exc

    async def get_statistics(self) -> dict[str, Any]:
        """Retrieve aggregate ledger statistics."""
        try:
            total_entries = await self.count()
            debit_result = await self.session.execute(select(func.coalesce(func.sum(Ledger.debit_amount), Decimal("0"))))
            credit_result = await self.session.execute(select(func.coalesce(func.sum(Ledger.credit_amount), Decimal("0"))))
            opening_result = await self.session.execute(select(func.coalesce(func.sum(Ledger.opening_balance), Decimal("0"))))
            closing_result = await self.session.execute(select(func.coalesce(func.sum(Ledger.closing_balance), Decimal("0"))))

            return {
                "total_entries": total_entries,
                "total_debit_amount": debit_result.scalar_one() or Decimal("0"),
                "total_credit_amount": credit_result.scalar_one() or Decimal("0"),
                "total_opening_balance": opening_result.scalar_one() or Decimal("0"),
                "total_closing_balance": closing_result.scalar_one() or Decimal("0"),
            }
        except SQLAlchemyError as exc:
            logger.exception("Failed to fetch ledger statistics")
            raise DatabaseException(detail="Unable to fetch ledger statistics.") from exc
