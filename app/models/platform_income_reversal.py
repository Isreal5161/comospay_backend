from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PlatformIncomeReversal(Base):
    """A traceable, idempotent refund or reversal against recognized platform income."""

    __tablename__ = "platform_income_reversals"

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_platform_income_reversal_amount_positive"),
        UniqueConstraint("event_reference", name="uq_platform_income_reversal_event_reference"),
        Index("ix_platform_income_reversal_income_created", "platform_income_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True)
    platform_income_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_income.id"),
        nullable=False,
        index=True,
    )
    event_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
