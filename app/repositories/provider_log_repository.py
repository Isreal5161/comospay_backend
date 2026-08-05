from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_log import ProviderLog


class ProviderLogRepository:
    """Repository for database access to provider log records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_provider_log(self, provider_log: ProviderLog) -> ProviderLog:
        """Create and persist a new provider log record."""
        self.session.add(provider_log)
        await self.session.flush()
        await self.session.refresh(provider_log)
        return provider_log

    async def get_by_id(self, provider_log_id: UUID) -> ProviderLog | None:
        """Retrieve a provider log by primary key."""
        result = await self.session.execute(select(ProviderLog).where(ProviderLog.id == provider_log_id))
        return result.scalar_one_or_none()

    async def get_by_reference(self, reference: str) -> ProviderLog | None:
        """Retrieve a provider log by correlation reference."""
        result = await self.session.execute(select(ProviderLog).where(ProviderLog.reference == reference))
        return result.scalar_one_or_none()

    async def get_provider_logs(
        self,
        *,
        provider_name: str | None = None,
        category: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[ProviderLog], int]:
        """Retrieve paginated provider logs with optional filtering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(ProviderLog)
        if provider_name:
            query = query.where(ProviderLog.provider_name == provider_name)
        if category:
            query = query.where(ProviderLog.category == category)
        if status:
            query = query.where(ProviderLog.status == status)

        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        order_column = getattr(ProviderLog, order_by, ProviderLog.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        provider_logs = list(result.scalars().all())
        return provider_logs, total

    async def get_failed_provider_logs(self, *, page: int = 1, page_size: int = 20) -> tuple[list[ProviderLog], int]:
        """Retrieve failed provider logs ordered by latest failure."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(ProviderLog).where(ProviderLog.success.is_(False))
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        result = await self.session.execute(
            query.order_by(ProviderLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        provider_logs = list(result.scalars().all())
        return provider_logs, total

    async def update_provider_log(self, provider_log: ProviderLog, **fields: Any) -> ProviderLog:
        """Update editable provider log fields in the database."""
        for field, value in fields.items():
            if hasattr(provider_log, field):
                setattr(provider_log, field, value)
        self.session.add(provider_log)
        await self.session.flush()
        await self.session.refresh(provider_log)
        return provider_log

    async def count_provider_logs(self) -> int:
        """Return the total number of provider log records."""
        result = await self.session.execute(select(func.count(ProviderLog.id)))
        return int(result.scalar_one() or 0)

    async def delete_provider_log(self, provider_log_id: UUID) -> None:
        """Delete a provider log record by primary key."""
        await self.session.execute(delete(ProviderLog).where(ProviderLog.id == provider_log_id))
        await self.session.flush()
