from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Notification(Base):
    """Persistent representation of a user notification."""

    __tablename__ = "notifications"

    __table_args__ = (
        Index("ix_notifications_user_status", "user_id", "status"),
        Index("ix_notifications_user_read", "user_id", "is_read"),
        Index("ix_notifications_channel_status", "channel", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique notification identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="Receiving user identifier.",
    )
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False, default="info", index=True, doc="Notification event type.")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general", index=True, doc="Notification category.")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Notification title.")
    message: Mapped[str] = mapped_column(Text, nullable=False, doc="Notification body content.")
    channel: Mapped[str] = mapped_column(String(50), nullable=False, default="in_app", index=True, doc="Delivery channel.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True, doc="Delivery status.")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the notification has been read.")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the notification is archived.")
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Optional reference for the related event.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional non-sensitive metadata.")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the message was sent.")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the message was delivered.")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the message was read.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="Record creation timestamp.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="Record update timestamp.",
    )
