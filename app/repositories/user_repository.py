from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """Repository for database access to user records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_dashboard_counts(
        self,
        *,
        current_day_start: datetime,
        current_day_end: datetime,
        previous_day_start: datetime,
        previous_day_end: datetime,
    ) -> dict[str, int]:
        """Return dashboard user counts using login and creation timestamps."""
        result = await self.session.execute(
            select(
                func.count(User.id).label("total_users"),
                func.count(
                    case(
                        (
                            (User.last_login_at >= current_day_start)
                            & (User.last_login_at < current_day_end),
                            User.id,
                        )
                    )
                ).label("active_users_today"),
                func.count(
                    case(
                        (
                            (User.created_at >= current_day_start)
                            & (User.created_at < current_day_end),
                            User.id,
                        )
                    )
                ).label("new_registrations_today"),
                func.count(
                    case(
                        (
                            (User.created_at >= previous_day_start)
                            & (User.created_at < previous_day_end),
                            User.id,
                        )
                    )
                ).label("new_registrations_previous_day"),
            )
        )
        row = result.one()
        return {
            "total_users": int(row.total_users or 0),
            "active_users_today": int(row.active_users_today or 0),
            "new_registrations_today": int(row.new_registrations_today or 0),
            "new_registrations_previous_day": int(row.new_registrations_previous_day or 0),
        }

    async def get_registration_counts_by_bucket(
        self,
        buckets: list[tuple[str, datetime, datetime]],
    ) -> dict[str, int]:
        """Count registrations grouped into the supplied half-open time buckets."""
        if not buckets:
            return {}

        bucket_case = case(
            *[
                ((User.created_at >= start) & (User.created_at < end), label)
                for label, start, end in buckets
            ],
            else_=None,
        )
        result = await self.session.execute(
            select(bucket_case.label("bucket"), func.count(User.id))
            .where(User.created_at >= buckets[0][1], User.created_at < buckets[-1][2])
            .group_by(bucket_case)
        )
        return {str(label): int(count or 0) for label, count in result.all() if label is not None}

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
        search: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[User], int]:
        """Retrieve filtered users using database-side search, ordering, and pagination."""
        sortable_columns = {
            "first_name": User.first_name,
            "last_name": User.last_name,
            "email": User.email,
            "username": User.username,
            "phone": User.phone,
            "status": User.status,
            "created_at": User.created_at,
            "updated_at": User.updated_at,
            "last_login_at": User.last_login_at,
        }
        order_column = sortable_columns.get(order_by)
        if order_column is None:
            raise ValueError(f"Unsupported user sort field: {order_by}")

        query = select(User)
        if status:
            query = query.where(User.status == status)
        if search and search.strip():
            escaped_search = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            search_pattern = f"%{escaped_search}%"
            query = query.where(
                User.first_name.ilike(search_pattern, escape="\\")
                | User.last_name.ilike(search_pattern, escape="\\")
                | User.email.ilike(search_pattern, escape="\\")
                | User.username.ilike(search_pattern, escape="\\")
                | User.phone.ilike(search_pattern, escape="\\")
            )

        filtered_query = query.order_by(None)
        count_result = await self.session.execute(
            select(func.count()).select_from(filtered_query.subquery())
        )
        total = int(count_result.scalar_one() or 0)

        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column, User.id).offset((page - 1) * page_size).limit(page_size)
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
