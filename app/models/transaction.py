from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Transaction(Base):
    """Persistent representation of a financial transaction."""

    __tablename__ = "transactions"

    __table_args__ = (
        UniqueConstraint("reference", name="uq_transactions_reference"),
        UniqueConstraint("provider_name", "provider_reference", name="uq_transactions_provider_ref"),
        Index("ix_transactions_user_created", "user_id", "created_at"),
        Index("ix_transactions_wallet_created", "wallet_id", "created_at"),
        Index("ix_transactions_status_created", "status", "created_at"),
        Index("ix_transactions_type_category", "transaction_type", "category"),
        Index("ix_transactions_provider_ref", "provider_name", "provider_reference"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique transaction identifier.")
    reference: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True, doc="Unique transaction reference.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="User associated with the transaction.",
    )
    wallet_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("wallets.id"),
        nullable=True,
        index=True,
        doc="Wallet involved in the transaction.",
    )
    transaction_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True, doc="Type of transaction.")
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True, doc="Transaction category for reporting.")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Transaction amount.")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="NGN", index=True, doc="Transaction currency.")
    charges: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Applicable charges.")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Total amount including charges.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True, doc="Transaction lifecycle status.")
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Payment or service provider name.")
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Provider-specific reference.")
    provider_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Provider transaction identifier.")
    external_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="External reference from a partner system.")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Short transaction description.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional non-sensitive transaction metadata.")
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
