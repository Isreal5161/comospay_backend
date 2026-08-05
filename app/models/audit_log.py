from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AuditLog(Base):
    """Persistent representation of an immutable audit event record."""

    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("ix_audit_logs_actor_action", "actor_type", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique audit log identifier.")
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False, default="system", index=True, doc="Type of actor that performed the action.")
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Reference to the actor, such as a user or admin identifier.")
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True, doc="Audit action name.")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Audit action category.")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Human-readable event description.")
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Affected resource type.")
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True, doc="Affected resource identifier.")
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True, doc="Request correlation identifier.")
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, doc="Source IP address.")
    device_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Device reference or identifier.")
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True, doc="User agent string.")
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True, doc="API endpoint or action reference.")
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Previous value metadata in JSON-compatible form.")
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True, doc="New value metadata in JSON-compatible form.")
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Concise summary of the change.")
    metadata_payload: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Optional additional metadata.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="Record creation timestamp.",
    )
