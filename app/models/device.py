from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Device(Base):
    """Persistent representation of a registered user device."""

    __tablename__ = "devices"

    __table_args__ = (
        UniqueConstraint("user_id", "device_fingerprint", name="uq_devices_user_fingerprint"),
        Index("ix_devices_user_active", "user_id", "is_active"),
        Index("ix_devices_user_trusted", "user_id", "is_trusted"),
        Index("ix_devices_trusted_active", "is_trusted", "is_active"),
        Index("ix_devices_last_seen", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique device identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="Owning user identifier.",
    )
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Human-readable device label.")
    device_type: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown", index=True, doc="Device category such as mobile, desktop, or tablet.")
    operating_system: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Operating system name.")
    os_version: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Operating system version.")
    app_version: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Application version reported by the client.")
    browser_name: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Browser name when applicable.")
    browser_version: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Browser version when applicable.")
    device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Stable device fingerprint for correlation.")
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True, doc="Most recent known IP address.")
    location_country: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Country code or name from geolocation metadata.")
    location_region: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Region or state from geolocation metadata.")
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="City from geolocation metadata.")
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the device is marked as trusted.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the device remains active.")
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the device has been revoked.")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        doc="First time the device was observed.",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, doc="Most recent activity timestamp.")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Revocation timestamp when applicable.")
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Reason for revoking the device.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional non-sensitive metadata payload.")
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
