from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin


class AdminRepository:
    """Repository for database access to admin records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, admin: Admin) -> Admin:
        """Create and persist a new admin record."""
        self.session.add(admin)
        await self.session.flush()
        await self.session.refresh(admin)
        return admin

    async def get_by_id(self, admin_id: UUID) -> Admin | None:
        """Retrieve an admin by primary key."""
        result = await self.session.execute(select(Admin).where(Admin.id == admin_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Admin | None:
        """Retrieve an admin by email address."""
        result = await self.session.execute(select(Admin).where(Admin.email == email))
        return result.scalar_one_or_none()

    async def get_all(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Admin], int]:
        """Retrieve paginated admin records with ordering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        order_column = getattr(Admin, order_by, Admin.created_at)
        if descending:
            order_column = order_column.desc()

        count_result = await self.session.execute(select(func.count(Admin.id)))
        total = int(count_result.scalar_one() or 0)

        result = await self.session.execute(
            select(Admin)
            .order_by(order_column)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        admins = list(result.scalars().all())
        return admins, total

    async def update(self, admin: Admin, **fields: Any) -> Admin:
        """Update editable admin fields in the database."""
        for field, value in fields.items():
            if hasattr(admin, field):
                setattr(admin, field, value)
        self.session.add(admin)
        await self.session.flush()
        await self.session.refresh(admin)
        return admin

    async def delete(self, admin_id: UUID) -> None:
        """Delete an admin record by primary key."""
        await self.session.execute(delete(Admin).where(Admin.id == admin_id))
        await self.session.flush()
