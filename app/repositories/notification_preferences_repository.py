from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings


class NotificationPreferencesRepository:
    """Store per-user notification preferences in the SystemSettings table as JSON.

    This lightweight adapter avoids introducing a new table while providing the
    persistence interface expected by NotificationPreferencesService.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _key_for_user(self, user_id: UUID) -> str:
        return f"notification:preferences:{user_id}"

    async def get_by_user_id(self, user_id: UUID) -> dict[str, Any] | None:
        key = await self._key_for_user(user_id)
        result = await self.session.execute(select(SystemSettings).where(SystemSettings.key == key))
        record = result.scalar_one_or_none()
        if not record or not record.value:
            return None
        try:
            return json.loads(record.value)
        except Exception:
            return None

    # Compatibility alias used by NotificationPreferencesService
    async def get_preferences_by_user_id(self, user_id: UUID) -> dict[str, Any] | None:
        return await self.get_by_user_id(user_id)

    async def update_preferences(self, user_id: UUID, preferences: dict[str, Any]) -> dict[str, Any]:
        key = await self._key_for_user(user_id)
        payload = {**preferences, "user_id": str(user_id)}
        await self.session.execute(
            update(SystemSettings).where(SystemSettings.key == key).values(value=json.dumps(payload))
        )
        await self.session.flush()
        return payload

    async def save_preferences(self, user_id: UUID, preferences: dict[str, Any]) -> dict[str, Any]:
        # Alias to update for compatibility
        return await self.update_preferences(user_id, preferences)

    async def create_preferences(self, preferences: dict[str, Any]) -> dict[str, Any]:
        # Expect preferences to include user_id
        user_id = preferences.get("user_id")
        if not user_id:
            raise ValueError("user_id is required to create preferences")
        key = await self._key_for_user(UUID(user_id) if not isinstance(user_id, str) else UUID(user_id))
        record = SystemSettings(
            id=uuid4(),
            key=key,
            value=json.dumps(preferences),
            value_type="json",
            category="notifications",
        )
        self.session.add(record)
        await self.session.flush()
        return preferences


__all__ = ["NotificationPreferencesRepository"]
