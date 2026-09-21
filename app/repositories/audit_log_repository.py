from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditLogRepository:
    """Repository for database access to immutable audit log records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_audit_log(self, audit_log: AuditLog) -> AuditLog:
        """Create and persist a new audit log record."""
        self.session.add(audit_log)
        await self.session.flush()
        await self.session.refresh(audit_log)
        return audit_log

    async def get_by_id(self, audit_log_id: UUID) -> AuditLog | None:
        """Retrieve an audit log by primary key."""
        result = await self.session.execute(select(AuditLog).where(AuditLog.id == audit_log_id))
        return result.scalar_one_or_none()

    async def get_user_audit_logs(
        self,
        *,
        user_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[AuditLog], int]:
        """Retrieve paginated audit logs for a specific user actor."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(AuditLog)
        if user_id is not None:
            query = query.where(AuditLog.actor_id == user_id)

        count_result = await self.session.execute(select(func.count(AuditLog.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(AuditLog, order_by, AuditLog.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        audit_logs = list(result.scalars().all())
        return audit_logs, total

    async def get_admin_audit_logs(
        self,
        *,
        admin_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[AuditLog], int]:
        """Retrieve paginated audit logs for admin actors."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(AuditLog).where(AuditLog.actor_type == "admin")
        if admin_id is not None:
            query = query.where(AuditLog.actor_id == admin_id)

        count_result = await self.session.execute(select(func.count(AuditLog.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(AuditLog, order_by, AuditLog.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        audit_logs = list(result.scalars().all())
        return audit_logs, total

    async def search_audit_logs(
        self,
        *,
        action: str | None = None,
        category: str | None = None,
        resource: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[AuditLog], int]:
        """Search audit logs with optional filters for action, category, resource, and time range."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(AuditLog)
        count_query = select(func.count(AuditLog.id))

        if action:
            query = query.where(AuditLog.action == action)
            count_query = count_query.where(AuditLog.action == action)
        if category:
            query = query.where(AuditLog.category == category)
            count_query = count_query.where(AuditLog.category == category)
        if resource:
            query = query.where(AuditLog.resource_type == resource)
            count_query = count_query.where(AuditLog.resource_type == resource)
        if start_date is not None:
            query = query.where(AuditLog.created_at >= start_date)
            count_query = count_query.where(AuditLog.created_at >= start_date)
        if end_date is not None:
            query = query.where(AuditLog.created_at <= end_date)
            count_query = count_query.where(AuditLog.created_at <= end_date)

        total_result = await self.session.execute(count_query)
        total = int(total_result.scalar_one() or 0)

        order_column = getattr(AuditLog, order_by, AuditLog.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        audit_logs = list(result.scalars().all())
        return audit_logs, total

    async def count_audit_logs(self) -> int:
        """Return the total number of audit log records."""
        result = await self.session.execute(select(func.count(AuditLog.id)))
        return int(result.scalar_one() or 0)

    async def get_transaction_timeline_events(self, *, transaction_id: UUID) -> list[AuditLog]:
        """Retrieve transaction-scoped audit events ordered by creation time."""
        result = await self.session.execute(
            select(AuditLog)
            .where(AuditLog.resource_type == "transaction")
            .where(AuditLog.resource_id == str(transaction_id))
            .order_by(AuditLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def delete_old_logs(self, older_than: datetime) -> int:
        """Delete audit logs older than the supplied timestamp."""
        result = await self.session.execute(delete(AuditLog).where(AuditLog.created_at < older_than))
        await self.session.flush()
        return int(result.rowcount or 0)
