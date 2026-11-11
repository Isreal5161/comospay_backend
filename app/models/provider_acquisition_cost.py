from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ProviderAcquisitionCost(Base):
    """Versioned administrator-configured provider acquisition-cost rule."""

    __tablename__ = "provider_acquisition_costs"

    __table_args__ = (
        CheckConstraint("cost_value > 0", name="ck_provider_acquisition_cost_value_positive"),
        CheckConstraint("effective_until IS NULL OR effective_until > effective_from", name="ck_provider_acquisition_cost_effective_range"),
        CheckConstraint("cost_method IN ('fixed_amount', 'percentage_of_selling_amount')", name="ck_provider_acquisition_cost_method"),
        Index(
            "ix_provider_acquisition_cost_scope",
            "provider_id",
            "service_type",
            "currency",
            "is_active",
            "effective_from",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("providers.id"), nullable=False, index=True)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    network_identifier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_identifier: Mapped[str | None] = mapped_column(String(180), nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    cost_method: Mapped[str] = mapped_column(String(50), nullable=False)
    cost_value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("admins.id"), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("admins.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class ProviderCostSnapshot(Base):
    """Immutable cost evidence snapshot for a successfully fulfilled transaction."""

    __tablename__ = "provider_cost_snapshots"

    __table_args__ = (
        CheckConstraint("selling_amount > 0", name="ck_provider_cost_snapshot_selling_positive"),
        CheckConstraint("cost_amount IS NULL OR cost_amount >= 0", name="ck_provider_cost_snapshot_cost_non_negative"),
        CheckConstraint(
            "source_class IN ('VERIFIED_ACTUAL', 'VERIFIED_CONTRACTUAL', 'ADMIN_CONFIGURED', 'UNAVAILABLE')",
            name="ck_provider_cost_snapshot_source_class",
        ),
        Index("ix_provider_cost_snapshot_provider_service", "provider_identifier", "service_type", "currency"),
        Index("ix_provider_cost_snapshot_classified_at", "source_class", "cost_snapshot_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("transactions.id"), nullable=False, unique=True, index=True)
    provider_id: Mapped[UUID | None] = mapped_column(ForeignKey("providers.id"), nullable=True, index=True)
    configuration_id: Mapped[UUID | None] = mapped_column(ForeignKey("provider_acquisition_costs.id"), nullable=True, index=True)
    provider_identifier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    network_identifier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_identifier: Mapped[str | None] = mapped_column(String(180), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selling_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    source_class: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    cost_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
