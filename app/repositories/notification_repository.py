from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification


class NotificationRepository:
    """Repository for database access to notification records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_notification(self, notification: Notification) -> Notification:
        """Create and persist a new notification record."""
        self.session.add(notification)
        await self.session.flush()
        await self.session.refresh(notification)
        return notification

    async def get_by_id(self, notification_id: UUID) -> Notification | None:
        """Retrieve a notification by primary key."""
        result = await self.session.execute(select(Notification).where(Notification.id == notification_id))
        return result.scalar_one_or_none()

    async def get_user_notifications(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Notification], int]:
        """Retrieve paginated notifications for a user."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Notification).where(Notification.user_id == user_id)
        count_result = await self.session.execute(select(func.count(Notification.id)).where(Notification.user_id == user_id))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(Notification, order_by, Notification.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        notifications = list(result.scalars().all())
        return notifications, total

    async def get_unread_notifications(self, *, user_id: UUID) -> list[Notification]:
        """Retrieve unread notifications for a user."""
        result = await self.session.execute(
            select(Notification)
            .where(Notification.user_id == user_id)
            .where(Notification.is_read.is_(False))
            .order_by(Notification.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_as_read(self, notification: Notification) -> Notification:
        """Mark a notification as read."""
        notification.is_read = True
        notification.read_at = datetime.now()
        self.session.add(notification)
        await self.session.flush()
        await self.session.refresh(notification)
        return notification

    async def mark_all_as_read(self, user_id: UUID) -> int:
        """Mark all notifications for a user as read."""
        notifications = await self.get_unread_notifications(user_id=user_id)
        for notification in notifications:
            notification.is_read = True
            notification.read_at = datetime.now()
            self.session.add(notification)
        await self.session.flush()
        return len(notifications)

    async def delete_notification(self, notification_id: UUID) -> None:
        """Delete a notification record by primary key."""
        await self.session.execute(delete(Notification).where(Notification.id == notification_id))
        await self.session.flush()
