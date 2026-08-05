from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device


class DeviceRepository:
    """Repository for database access to device records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_device(self, device: Device) -> Device:
        """Create and persist a new device record."""
        self.session.add(device)
        await self.session.flush()
        await self.session.refresh(device)
        return device

    async def get_device_by_id(self, device_id: UUID) -> Device | None:
        """Retrieve a device by primary key."""
        result = await self.session.execute(select(Device).where(Device.id == device_id))
        return result.scalar_one_or_none()

    async def get_user_devices(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "last_seen_at",
        descending: bool = True,
    ) -> tuple[list[Device], int]:
        """Retrieve paginated devices belonging to a user, ordered by recent activity."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Device).where(Device.user_id == user_id)
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        order_column = getattr(Device, order_by, Device.last_seen_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        devices = list(result.scalars().all())
        return devices, total

    async def get_device_by_identifier(self, device_identifier: str) -> Device | None:
        """Retrieve a device using a device identifier reference."""
        result = await self.session.execute(
            select(Device).where(Device.device_fingerprint == device_identifier)
        )
        return result.scalar_one_or_none()

    async def update_device(self, device: Device, **fields: Any) -> Device:
        """Update editable device fields in the database."""
        for field, value in fields.items():
            if hasattr(device, field):
                setattr(device, field, value)
        self.session.add(device)
        await self.session.flush()
        await self.session.refresh(device)
        return device

    async def delete_device(self, device_id: UUID) -> None:
        """Delete a device record by primary key."""
        await self.session.execute(delete(Device).where(Device.id == device_id))
        await self.session.flush()

    async def deactivate_device(self, device: Device, *, reason: str | None = None) -> Device:
        """Update a device record to a deactivated state."""
        device.is_active = False
        device.is_revoked = True
        device.revoked_reason = reason
        device.revoked_at = datetime.now()
        self.session.add(device)
        await self.session.flush()
        await self.session.refresh(device)
        return device
