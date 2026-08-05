from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SystemSettings(Base):
    """Persistent representation of an administrator-controlled system setting."""

    __tablename__ = "system_settings"

    __table_args__ = (
        UniqueConstraint("key", name="uq_system_settings_key"),
        Index("ix_system_settings_category_active", "category", "is_active"),
        Index("ix_system_settings_key_active", "key", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique setting identifier.")
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True, doc="Unique setting key.")
    value: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Stored setting value.")
    value_type: Mapped[str] = mapped_column(String(50), nullable=False, default="string", index=True, doc="Value data type.")
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="general", index=True, doc="Settings category.")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Human-readable description.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the setting is active.")
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Reference to the admin who created the setting.")
    updated_by: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Reference to the admin who last updated the setting.")
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
