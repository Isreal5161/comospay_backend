from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import Admin
from app.models.support import SupportMessage, SupportTicket
from app.models.user import User


class SupportTicketRepository:
    """Repository for persisted support tickets and messages."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_ticket(self, ticket: SupportTicket) -> SupportTicket:
        self.session.add(ticket)
        await self.session.flush()
        await self.session.refresh(ticket)
        return ticket

    async def update_ticket(self, ticket: SupportTicket) -> SupportTicket:
        self.session.add(ticket)
        await self.session.flush()
        await self.session.refresh(ticket)
        return ticket

    async def create_message(self, message: SupportMessage) -> SupportMessage:
        self.session.add(message)
        await self.session.flush()
        await self.session.refresh(message)
        return message

    async def get_by_id(self, ticket_id: UUID) -> SupportTicket | None:
        result = await self.session.execute(
            select(SupportTicket)
            .options(
                selectinload(SupportTicket.user),
                selectinload(SupportTicket.assignee),
                selectinload(SupportTicket.messages).selectinload(SupportMessage.customer),
                selectinload(SupportTicket.messages).selectinload(SupportMessage.admin),
            )
            .where(SupportTicket.id == ticket_id)
        )
        return result.scalar_one_or_none()

    async def list_tickets(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> tuple[list[SupportTicket], int]:
        stmt = select(SupportTicket).options(
            selectinload(SupportTicket.user),
            selectinload(SupportTicket.assignee),
            selectinload(SupportTicket.messages).selectinload(SupportMessage.customer),
            selectinload(SupportTicket.messages).selectinload(SupportMessage.admin),
        )
        count_stmt = select(func.count(SupportTicket.id))

        if search:
            needle = f"%{search.strip()}%"
            user_search = (
                select(User.id)
                .where(
                    or_(
                        User.first_name.ilike(needle),
                        User.last_name.ilike(needle),
                        User.email.ilike(needle),
                        User.username.ilike(needle),
                    )
                )
                .subquery()
            )
            stmt = stmt.where(
                or_(
                    SupportTicket.subject.ilike(needle),
                    SupportTicket.category.ilike(needle),
                    SupportTicket.status.ilike(needle),
                    SupportTicket.priority.ilike(needle),
                    SupportTicket.user_id.in_(select(user_search.c.id)),
                )
            )
            count_stmt = count_stmt.where(
                or_(
                    SupportTicket.subject.ilike(needle),
                    SupportTicket.category.ilike(needle),
                    SupportTicket.status.ilike(needle),
                    SupportTicket.priority.ilike(needle),
                    SupportTicket.user_id.in_(select(user_search.c.id)),
                )
            )

        if status:
            stmt = stmt.where(SupportTicket.status == status)
            count_stmt = count_stmt.where(SupportTicket.status == status)
        if priority:
            stmt = stmt.where(SupportTicket.priority == priority)
            count_stmt = count_stmt.where(SupportTicket.priority == priority)

        total_result = await self.session.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)

        stmt = stmt.order_by(SupportTicket.updated_at.desc(), SupportTicket.created_at.desc(), SupportTicket.id)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_valid_admin(self, admin_id: UUID) -> Admin | None:
        from sqlalchemy import select

        result = await self.session.execute(select(Admin).where(Admin.id == admin_id))
        return result.scalar_one_or_none()

    async def get_ticket_messages(self, ticket_id: UUID) -> list[SupportMessage]:
        result = await self.session.execute(
            select(SupportMessage)
            .where(SupportMessage.ticket_id == ticket_id)
            .order_by(SupportMessage.created_at.asc())
            .options(selectinload(SupportMessage.customer), selectinload(SupportMessage.admin))
        )
        return list(result.scalars().all())
