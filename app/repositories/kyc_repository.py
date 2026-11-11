from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kyc import KYC
from app.models.user import User


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

    async def get_user_submission_for_update(self, *, user_id: UUID, kyc_id: UUID) -> KYC | None:
        """Retrieve and lock the specified submission only when it belongs to the user."""
        result = await self.session.execute(
            select(KYC)
            .where(KYC.id == kyc_id, KYC.user_id == user_id)
            .with_for_update()
        )
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
        count_result = await self.session.execute(select(func.count(KYC.id)).where(KYC.user_id == user_id))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(KYC, order_by, KYC.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column, KYC.id.desc()).offset((page - 1) * page_size).limit(page_size)
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
        count_result = await self.session.execute(select(func.count(KYC.id)).where(KYC.verification_status == "pending"))
        total = int(count_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(KYC.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        kycs = list(result.scalars().all())
        return kycs, total

    async def get_admin_kyc(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
        document_type: str | None = None,
    ) -> tuple[list[tuple[KYC, dict[str, str | None]]], int]:
        """Return globally paginated KYC rows with only safe, joined user fields."""
        filters = []
        if status and status.strip():
            filters.append(KYC.verification_status == status.strip())
        if document_type and document_type.strip():
            filters.append(KYC.document_type == document_type.strip())
        if search and search.strip():
            search_term = search.strip()
            pattern = f"%{search_term}%"
            filters.append(
                or_(
                    cast(KYC.id, String).ilike(pattern),
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    User.email.ilike(pattern),
                    User.phone.ilike(pattern),
                )
            )

        count_result = await self.session.execute(
            select(func.count(KYC.id)).select_from(KYC).outerjoin(User, User.id == KYC.user_id).where(*filters)
        )
        total = int(count_result.scalar_one() or 0)
        result = await self.session.execute(
            select(
                KYC,
                User.first_name.label("user_first_name"),
                User.last_name.label("user_last_name"),
                User.email.label("user_email"),
                User.phone.label("user_phone"),
            )
            .outerjoin(User, User.id == KYC.user_id)
            .where(*filters)
            .order_by(KYC.created_at.desc(), KYC.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        records = [
            (
                row[0],
                {
                    "user_first_name": row.user_first_name,
                    "user_last_name": row.user_last_name,
                    "user_email": row.user_email,
                    "user_phone": row.user_phone,
                },
            )
            for row in result.all()
        ]
        return records, total

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
