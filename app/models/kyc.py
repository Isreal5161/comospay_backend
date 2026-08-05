from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class KYC(Base):
    """Persistent representation of a user's KYC verification record."""

    __tablename__ = "kyc"

    __table_args__ = (
        Index("ix_kyc_user_status", "user_id", "verification_status"),
        Index("ix_kyc_status_level", "verification_status", "verification_level"),
        Index("ix_kyc_document_status", "document_type", "document_verification_status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique KYC record identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="Owning user identifier.",
    )
    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        index=True,
        doc="Current KYC verification status.",
    )
    verification_level: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="basic",
        index=True,
        doc="Requested verification level.",
    )
    document_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Primary document category.")
    document_reference: Mapped[str | None] = mapped_column(String(500), nullable=True, doc="Reference or storage path to the submitted document.")
    document_verification_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, doc="Verification status of the submitted document.")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp of initial submission.")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True, doc="Timestamp of the latest review.")
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Reference to the reviewer or admin.")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp of approval.")
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp of rejection.")
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Reason for rejection when applicable.")
    compliance_notes: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Operational compliance notes.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the KYC record remains active.")
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the KYC record is verified.")
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
