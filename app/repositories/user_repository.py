from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """Repository for database access to user records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_user(self, user: User) -> User:
        """Create and persist a new user record."""
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Retrieve a user by primary key."""
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Retrieve a user by email address."""
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> User | None:
        """Retrieve a user by phone number."""
        result = await self.session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    async def get_all_users(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[User], int]:
        """Retrieve paginated users with optional filtering and ordering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(User)
        if status:
            query = query.where(User.status == status)

        count_result = await self.session.execute(select(func.count(User.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(User, order_by, User.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        users = list(result.scalars().all())
        return users, total

    async def update_user(self, user: User, **fields: Any) -> User:
        """Update editable user fields in the database."""
        for field, value in fields.items():
            if hasattr(user, field):
                setattr(user, field, value)
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update_last_login(self, user: User, *, last_login_at: datetime | None = None) -> User:
        """Persist the user's most recent login timestamp."""
        user.last_login_at = last_login_at or datetime.now()
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def delete_user(self, user_id: UUID) -> None:
        """Delete a user record by primary key."""
        await self.session.execute(delete(User).where(User.id == user_id))
        await self.session.flush()
