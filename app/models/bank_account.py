from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BankAccount(Base):
    """Persistent representation of a user-linked bank or virtual account."""

    __tablename__ = "bank_accounts"

    __table_args__ = (
        Index("ix_bank_accounts_user_status", "user_id", "status"),
        Index("ix_bank_accounts_provider_ref", "provider_name", "provider_reference"),
        Index("ix_bank_accounts_active", "is_active", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique bank account identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="Owning user identifier.",
    )
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Account holder name.")
    account_number_encrypted: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Encrypted bank account number.")
    account_number_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, doc="Keyed bank account lookup fingerprint.")
    account_number_prefix: Mapped[str | None] = mapped_column(String(2), nullable=True, doc="Non-sensitive masking prefix.")
    account_number_last4: Mapped[str | None] = mapped_column(String(4), nullable=True, doc="Last four account-number digits.")
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Bank name.")
    bank_code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, doc="Bank code.")
    account_type: Mapped[str | None] = mapped_column(String(50), nullable=True, doc="Account type such as savings or current.")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the account is the default account.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the account remains active.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True, doc="Lifecycle status of the account.")
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Payment provider name.")
    provider_type: Mapped[str | None] = mapped_column(String(100), nullable=True, doc="Provider account type.")
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Provider-specific reference identifier.")
    provider_customer_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Provider customer reference.")
    virtual_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, doc="Virtual account number when applicable.")
    virtual_bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Virtual bank name when applicable.")
    account_creation_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True, doc="Provider-side account creation status.")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp of verification.")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the account was activated.")
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, doc="Timestamp when the account was deactivated.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional non-sensitive provider metadata.")
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
