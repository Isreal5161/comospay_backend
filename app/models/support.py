from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class SupportTicket(Base):
    """Persistent support ticket for customer issues."""

    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting_customer', 'resolved', 'closed')",
            name="ck_support_tickets_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_support_tickets_priority",
        ),
        Index("ix_support_tickets_user_status", "user_id", "status"),
        Index("ix_support_tickets_status_updated", "status", "updated_at"),
        Index("ix_support_tickets_priority_updated", "priority", "updated_at"),
        Index("ix_support_tickets_assignee_updated", "assignee_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    initial_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", server_default=text("'open'"))
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="normal", server_default=text("'normal'"))
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general", server_default=text("'general'"), index=True)
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("admins.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    assignee: Mapped["Admin"] = relationship("Admin", foreign_keys=[assignee_id])
    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at.asc()",
    )


class SupportMessage(Base):
    """Individual customer or admin message within a support ticket."""

    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint(
            "author_type IN ('customer', 'admin')",
            name="ck_support_messages_author_type",
        ),
        CheckConstraint(
            "((author_type = 'customer' AND customer_id IS NOT NULL AND admin_id IS NULL) OR (author_type = 'admin' AND admin_id IS NOT NULL AND customer_id IS NULL))",
            name="ck_support_messages_author_identity",
        ),
        Index("ix_support_messages_ticket_created", "ticket_id", "created_at"),
        Index("ix_support_messages_ticket_internal_created", "ticket_id", "is_internal", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    admin_id: Mapped[UUID | None] = mapped_column(ForeignKey("admins.id"), nullable=True, index=True)
    author_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")
    customer: Mapped["User"] = relationship("User", foreign_keys=[customer_id])
    admin: Mapped["Admin"] = relationship("Admin", foreign_keys=[admin_id])
