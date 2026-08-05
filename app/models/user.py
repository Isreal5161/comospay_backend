from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class User(Base):
    """Persistent representation of a registered customer account."""

    __tablename__ = "users"

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("username", name="uq_users_username"),
        Index("ix_users_status_created", "status", "created_at"),
        Index("ix_users_email_status", "email", "status"),
        Index("ix_users_phone_status", "phone", "status"),
        Index("ix_users_is_active_verified", "is_active", "email_verified"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique user identifier.")
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="User first name.")
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="User last name.")
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True, doc="Primary email address.")
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True, doc="Primary phone number.")
    username: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True, doc="Optional unique username.")
    profile_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True, doc="Profile image reference.")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Stored password hash.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True, doc="Account lifecycle status.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the account is active.")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the account is blocked.")
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the account is suspended.")
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the email address is verified.")
    phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the phone number is verified.")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, doc="Whether multi-factor authentication is enabled.")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Most recent successful login timestamp.")
    last_password_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Most recent password change timestamp.")
    account_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Account lock expiration timestamp.")
    failed_login_attempts: Mapped[int] = mapped_column(default=0, nullable=False, doc="Count of consecutive failed login attempts.")
    customer_tier: Mapped[str | None] = mapped_column(String(50), nullable=True, default="standard", index=True, doc="Customer tier for future segmentation.")
    kyc_level: Mapped[str | None] = mapped_column(String(50), nullable=True, default="basic", index=True, doc="KYC level for future compliance flows.")
    referral_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, doc="Referral code for future referral features.")
    locale: Mapped[str | None] = mapped_column(String(20), nullable=True, doc="Preferred locale.")
    timezone_name: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Preferred timezone.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional non-sensitive profile metadata.")
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
