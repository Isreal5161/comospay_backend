from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OTP(Base):
    """Persistent representation of a one-time password record."""

    __tablename__ = "otps"

    __table_args__ = (
        Index("ix_otps_user_purpose", "user_id", "purpose"),
        Index("ix_otps_status_created", "is_used", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique OTP record identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="Owning user identifier.",
    )
    purpose: Mapped[str] = mapped_column(String(100), nullable=False, default="verification", index=True, doc="OTP purpose or use case.")
    otp_reference: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Optional external or internal OTP reference.")
    hashed_value: Mapped[str] = mapped_column(String(255), nullable=False, doc="Hashed OTP value.")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True, doc="Expiration timestamp.")
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the OTP has already been used.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the OTP record remains active.")
    attempts: Mapped[int] = mapped_column(default=0, nullable=False, doc="Number of verification attempts.")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the OTP was verified.")
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
