from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider


class ProviderRepository:
    """Repository for database access to provider records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_provider(self, provider: Provider) -> Provider:
        """Create and persist a new provider record."""
        self.session.add(provider)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def get_provider_by_id(self, provider_id: UUID) -> Provider | None:
        """Retrieve a provider by primary key."""
        result = await self.session.execute(select(Provider).where(Provider.id == provider_id))
        return result.scalar_one_or_none()

    async def get_provider_by_name(self, name: str) -> Provider | None:
        """Retrieve a provider by name."""
        result = await self.session.execute(select(Provider).where(Provider.name == name))
        return result.scalar_one_or_none()

    async def get_active_providers(
        self,
        *,
        category: str | None = None,
        service_type: str | None = None,
    ) -> list[Provider]:
        """Retrieve active providers with optional filtering."""
        query = select(Provider).where(Provider.is_active.is_(True))
        if category:
            query = query.where(Provider.category == category)
        if service_type:
            query = query.where(Provider.category == service_type)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_providers(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        status: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Provider], int]:
        """Retrieve paginated providers with optional filtering."""
        if not isinstance(page, int) or page < 1:
            page = 1
        if not isinstance(page_size, int) or page_size < 1:
            page_size = 20

        query = select(Provider)
        if category:
            query = query.where(Provider.category == category)
        if status:
            query = query.where(Provider.status == status)

        count_result = await self.session.execute(select(func.count(Provider.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(Provider, order_by, Provider.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        providers = list(result.scalars().all())
        return providers, total

    async def update_provider(self, provider: Provider, **fields: Any) -> Provider:
        """Update editable provider fields in the database."""
        for field, value in fields.items():
            if hasattr(provider, field):
                if field == "metadata_payload" and value is not None and not isinstance(value, str):
                    value = json.dumps(value, default=str)
                setattr(provider, field, value)
        self.session.add(provider)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def disable_provider(self, provider: Provider, *, reason: str | None = None) -> Provider:
        """Persist a provider entity prepared by domain services."""
        _ = reason
        self.session.add(provider)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a provider record by primary key."""
        await self.session.execute(delete(Provider).where(Provider.id == provider_id))
        await self.session.flush()
