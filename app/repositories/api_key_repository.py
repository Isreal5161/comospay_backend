from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import APIKey


class APIKeyRepository:
    """Repository for database operations on API key records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_api_key(self, api_key: APIKey) -> APIKey:
        """Create and persist a new API key record."""
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key

    async def get_by_id(self, api_key_id: UUID) -> APIKey | None:
        """Retrieve an API key by primary key."""
        result = await self.session.execute(select(APIKey).where(APIKey.id == api_key_id))
        return result.scalar_one_or_none()

    async def get_by_key_hash(self, hashed_key: str) -> APIKey | None:
        """Retrieve an API key by its stored hash value."""
        result = await self.session.execute(select(APIKey).where(APIKey.hashed_key == hashed_key))
        return result.scalar_one_or_none()

    async def get_user_api_keys(
        self,
        *,
        owner_id: str,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[APIKey], int]:
        """Retrieve paginated API keys belonging to a user or owner."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        order_column = getattr(APIKey, order_by, APIKey.created_at)
        if descending:
            order_column = order_column.desc()

        count_result = await self.session.execute(select(APIKey).where(APIKey.owner_id == owner_id))
        total = len(count_result.scalars().all())

        result = await self.session.execute(
            select(APIKey)
            .where(APIKey.owner_id == owner_id)
            .order_by(order_column)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        api_keys = list(result.scalars().all())
        return api_keys, total

    async def update_api_key(self, api_key: APIKey, **fields: Any) -> APIKey:
        """Update editable API key fields in the database."""
        for field, value in fields.items():
            if hasattr(api_key, field):
                setattr(api_key, field, value)
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key

    async def revoke_api_key(self, api_key: APIKey, *, reason: str | None = None) -> APIKey:
        """Update the API key record to a revoked state."""
        api_key.status = "revoked"
        api_key.is_active = False
        api_key.revoked_at = datetime.now()
        api_key.revoked_reason = reason
        self.session.add(api_key)
        await self.session.flush()
        await self.session.refresh(api_key)
        return api_key

    async def delete_api_key(self, api_key_id: UUID) -> None:
        """Delete an API key record by primary key."""
        await self.session.execute(delete(APIKey).where(APIKey.id == api_key_id))
        await self.session.flush()
