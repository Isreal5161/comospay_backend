from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings


class SystemSettingsRepository:
    """Repository for database access to system setting records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_setting(self, setting: SystemSettings) -> SystemSettings:
        """Create and persist a new system setting record."""
        self.session.add(setting)
        await self.session.flush()
        await self.session.refresh(setting)
        return setting

    async def get_by_id(self, setting_id: UUID) -> SystemSettings | None:
        """Retrieve a system setting by primary key."""
        result = await self.session.execute(select(SystemSettings).where(SystemSettings.id == setting_id))
        return result.scalar_one_or_none()

    async def get_by_key(self, key: str) -> SystemSettings | None:
        """Retrieve a system setting by its unique key."""
        result = await self.session.execute(select(SystemSettings).where(SystemSettings.key == key))
        return result.scalar_one_or_none()

    async def get_all_settings(
        self,
        *,
        category: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SystemSettings], int]:
        """Retrieve paginated system settings with optional filtering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(SystemSettings)
        count_query = select(func.count(SystemSettings.id))

        if category:
            query = query.where(SystemSettings.category == category)
            count_query = count_query.where(SystemSettings.category == category)
        if is_active is not None:
            query = query.where(SystemSettings.is_active.is_(is_active))
            count_query = count_query.where(SystemSettings.is_active.is_(is_active))

        total_result = await self.session.execute(count_query)
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(SystemSettings.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        settings = list(result.scalars().all())
        return settings, total

    async def update_setting(
        self,
        setting_id: UUID,
        *,
        value: Any = object(),
        description: Any = object(),
        is_active: Any = object(),
    ) -> SystemSettings | None:
        """Update permitted system setting fields in the database."""
        sentinel = object()
        update_values: dict[str, Any] = {}

        if value is not sentinel:
            update_values["value"] = value
        if description is not sentinel:
            update_values["description"] = description
        if is_active is not sentinel:
            update_values["is_active"] = is_active

        if not update_values:
            return await self.get_by_id(setting_id)

        await self.session.execute(
            update(SystemSettings)
            .where(SystemSettings.id == setting_id)
            .values(**update_values)
        )
        await self.session.flush()
        return await self.get_by_id(setting_id)

    async def delete_setting(self, setting_id: UUID) -> None:
        """Delete a system setting record by primary key."""
        await self.session.execute(delete(SystemSettings).where(SystemSettings.id == setting_id))
        await self.session.flush()

    async def exists_by_key(self, key: str) -> bool:
        """Check whether a system setting exists for the given key."""
        result = await self.session.execute(select(exists().where(SystemSettings.key == key)))
        return bool(result.scalar_one())
