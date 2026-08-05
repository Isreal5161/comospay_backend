from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ProviderLog(Base):
    """Persistent representation of an external provider interaction log."""

    __tablename__ = "provider_logs"

    __table_args__ = (
        Index("ix_provider_logs_provider_status", "provider_name", "status"),
        Index("ix_provider_logs_service_status", "service_name", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique provider log identifier.")
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True, doc="Provider name.")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Provider category.")
    service_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Service or action name.")
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Reference for correlating the interaction.")
    request_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Sanitized request metadata payload.")
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Sanitized response metadata payload.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True, doc="Interaction status.")
    response_code: Mapped[str | None] = mapped_column(String(50), nullable=True, doc="Provider response code.")
    success: Mapped[bool] = mapped_column(default=False, nullable=False, index=True, doc="Whether the interaction succeeded.")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, doc="Elapsed response duration in milliseconds.")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="Number of retries attempted.")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Error detail when applicable.")
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
