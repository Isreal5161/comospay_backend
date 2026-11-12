from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class FraudInvestigation(Base):
    """Persisted review state for a signal derived from existing evidence."""

    __tablename__ = "fraud_investigations"
    __table_args__ = (
        UniqueConstraint("signal_type", "signal_reference", name="uq_fraud_investigation_signal"),
        CheckConstraint(
            "signal_type IN ('transaction', 'account_security')",
            name="ck_fraud_investigation_signal_type",
        ),
        CheckConstraint(
            "state IN ('open', 'in_review', 'escalated', 'dismissed', 'resolved')",
            name="ck_fraud_investigation_state",
        ),
        Index("ix_fraud_investigations_state_updated", "state", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_reference: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    evidence_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    reviewed_by_admin_id: Mapped[UUID] = mapped_column(ForeignKey("admins.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class FraudInvestigationEvent(Base):
    """Immutable history of each administrator decision on an investigation."""

    __tablename__ = "fraud_investigation_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('review', 'dismiss', 'escalate', 'resolve')",
            name="ck_fraud_investigation_event_action",
        ),
        Index("ix_fraud_investigation_events_case_created", "investigation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("fraud_investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admins.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
