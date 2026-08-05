from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kyc import KYC


class KYCRepository:
    """Repository for database access to KYC records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_kyc(self, kyc: KYC) -> KYC:
        """Create and persist a new KYC record."""
        self.session.add(kyc)
        await self.session.flush()
        await self.session.refresh(kyc)
        return kyc

    async def get_by_id(self, kyc_id: UUID) -> KYC | None:
        """Retrieve a KYC record by primary key."""
        result = await self.session.execute(select(KYC).where(KYC.id == kyc_id))
        return result.scalar_one_or_none()

    async def get_user_kyc(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[KYC], int]:
        """Retrieve paginated KYC records for a user."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(KYC).where(KYC.user_id == user_id)
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        order_column = getattr(KYC, order_by, KYC.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        kycs = list(result.scalars().all())
        return kycs, total

    async def get_pending_kyc(self, *, page: int = 1, page_size: int = 20) -> tuple[list[KYC], int]:
        """Retrieve pending KYC records for admin review workflows."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(KYC).where(KYC.verification_status == "pending")
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        result = await self.session.execute(
            query.order_by(KYC.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        kycs = list(result.scalars().all())
        return kycs, total

    async def update_kyc(self, kyc: KYC, **fields: Any) -> KYC:
        """Update editable KYC fields in the database."""
        for field, value in fields.items():
            if hasattr(kyc, field):
                setattr(kyc, field, value)
        self.session.add(kyc)
        await self.session.flush()
        await self.session.refresh(kyc)
        return kyc

    async def delete_kyc(self, kyc_id: UUID) -> None:
        """Delete a KYC record by primary key."""
        await self.session.execute(delete(KYC).where(KYC.id == kyc_id))
        await self.session.flush()
