from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PlatformIncome(Base):
    """Persisted income whose retained ownership was verified by its source service."""

    __tablename__ = "platform_income"

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_platform_income_amount_non_negative"),
        CheckConstraint("selling_amount > 0", name="ck_platform_income_selling_amount_positive"),
        CheckConstraint("provider_cost >= 0", name="ck_platform_income_provider_cost_non_negative"),
        CheckConstraint(
            "amount = CASE WHEN gross_margin > 0 THEN gross_margin ELSE 0 END",
            name="ck_platform_income_amount_matches_positive_margin",
        ),
        CheckConstraint("reversal_amount >= 0", name="ck_platform_income_reversal_amount_non_negative"),
        CheckConstraint("reversal_amount <= amount", name="ck_platform_income_reversal_not_over_amount"),
        UniqueConstraint("transaction_id", "source_type", "income_category", name="uq_platform_income_transaction_source_category"),
        Index("ix_platform_income_transaction", "transaction_id", "status", "currency"),
        Index("ix_platform_income_status_created", "status", "created_at"),
        Index("ix_platform_income_currency_created", "currency", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True)
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("transactions.id"),
        nullable=False,
        index=True,
        doc="Transaction that generated the retained platform fee.",
    )
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        doc="Source service or record that proves retained ownership.",
    )
    source_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Authoritative transaction-specific provider-cost evidence reference.",
    )
    income_category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        doc="Service category for the transaction-specific margin.",
    )
    provider_identifier: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Provider that fulfilled the service transaction.",
    )
    provider_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Provider transaction reference used to attribute the actual cost.",
    )
    selling_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        doc="Customer selling price from the source transaction.",
    )
    provider_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        doc="Actual provider cost attributable to the source transaction.",
    )
    gross_margin: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        doc="Selling price less actual provider cost; may represent a loss.",
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="NGN",
        index=True,
        doc="Currency for the recognized retained income.",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Amount recognized as retained platform income.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="unverified",
        index=True,
        doc="Recognition lifecycle status for the income record.",
    )
    recognized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="When the retained income was recognized.",
    )
    reversal_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Total amount reversed from this recognized income record.",
    )
    reversal_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Traceable reference for the reversal event when applicable.",
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
        doc="Timestamp of the most recent update.",
    )
