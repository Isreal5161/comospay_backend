from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.wallet import Wallet


class VirtualAccount(Base):
    """Provider-issued virtual bank account linked to a wallet.

    This model stores provider-issued account information separately from wallet
    balances and lifecycle state. It is intended for payment-provider account
    provisioning and reconciliation scenarios.
    """

    __tablename__ = "virtual_accounts"

    __table_args__ = (
        UniqueConstraint("provider", "account_number", name="uq_virtual_accounts_provider_account_number"),
        UniqueConstraint("provider_reference", name="uq_virtual_accounts_provider_reference"),
        UniqueConstraint("provider_account_id", name="uq_virtual_accounts_provider_account_id"),
        Index("ix_virtual_accounts_wallet_status", "wallet_id", "status"),
        Index("ix_virtual_accounts_provider_status", "provider", "status"),
        Index("ix_virtual_accounts_wallet_active_primary", "wallet_id", "is_active", "is_primary"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique virtual account identifier.")
    wallet_id: Mapped[UUID] = mapped_column(
        ForeignKey("wallets.id"),
        nullable=False,
        index=True,
        doc="Wallet that owns this virtual account.",
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        doc="User associated with the virtual account when available.",
    )
    provider: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="provider",
        index=True,
        doc="Provider name such as flutterwave, paystack, monnify, or korapay.",
    )
    account_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        doc="Provider-issued account number.",
    )
    account_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Display name associated with the virtual account.",
    )
    bank_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Bank name associated with the virtual account.",
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
        doc="Provider reference for the account.",
    )
    provider_customer_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        doc="Provider customer identifier when available.",
    )
    provider_account_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
        doc="Provider account identifier when available.",
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="NGN",
        index=True,
        doc="Currency for the virtual account.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True,
        doc="Lifecycle status of the virtual account.",
    )
    kyc_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="NOT_STARTED",
        index=True,
        doc="KYC verification status for the account.",
    )
    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True,
        doc="Provider verification status.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        doc="Whether the virtual account is active.",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        doc="Whether the virtual account is the primary account for its wallet.",
    )
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
        default=dict,
        doc="Additional provider-specific metadata.",
    )

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
    # Provisioning lifecycle fields
    retry_count: Mapped[int] = mapped_column(
        "retry_count",
        default=0,
        nullable=False,
        doc="Number of provisioning retry attempts.",
    )
    last_retry_at: Mapped[datetime | None] = mapped_column(
        "last_retry_at",
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of the last provisioning retry attempt.",
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        "next_retry_at",
        DateTime(timezone=True),
        nullable=True,
        index=True,
        doc="Scheduled time for the next provisioning retry.",
    )
    last_error: Mapped[str | None] = mapped_column(
        "last_error",
        Text,
        nullable=True,
        doc="Last provisioning error message.",
    )
    provisioned_at: Mapped[datetime | None] = mapped_column(
        "provisioned_at",
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when provisioning succeeded.",
    )

    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        foreign_keys=[wallet_id],
        back_populates="virtual_accounts",
        lazy="selectin",
    )
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id], lazy="selectin")

    def __getattr__(self, name: str) -> Any:
        if name == "metadata":
            return self.metadata_payload
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "metadata":
            object.__setattr__(self, "metadata_payload", value)
            return
        object.__setattr__(self, name, value)
