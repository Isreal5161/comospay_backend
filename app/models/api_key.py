from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class APIKey(Base):
    """Persistent representation of a system API key."""

    __tablename__ = "api_keys"

    __table_args__ = (
        UniqueConstraint("key_id", name="uq_api_keys_key_id"),
        UniqueConstraint("name", name="uq_api_keys_name"),
        Index("ix_api_keys_status_expires_at", "status", "expires_at"),
        Index("ix_api_keys_owner_env_status", "owner_type", "environment", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True)
    key_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_type: Mapped[str] = mapped_column(String(50), nullable=False, default="system", index=True)
    owner_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    environment: Mapped[str] = mapped_column(String(50), nullable=False, default="production", index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hashed_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prefix: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False, index=True)
