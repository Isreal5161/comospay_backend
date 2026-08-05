from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditLogBase(BaseModel):
    """Shared audit log fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    action: str = Field(..., max_length=100, description="Audit action name.")
    category: str | None = Field(default=None, max_length=100, description="Audit action category.")
    resource_type: str | None = Field(default=None, max_length=100, description="Affected resource type.")
    description: str | None = Field(default=None, description="Human-readable event description.")


class AuditLogCreate(AuditLogBase):
    """Schema for creating an immutable audit log entry."""

    actor_type: str = Field(default="system", max_length=50, description="Type of actor that performed the action.")
    actor_id: str | None = Field(default=None, max_length=100, description="Reference to the actor, such as a user or admin identifier.")
    resource_id: str | None = Field(default=None, max_length=255, description="Affected resource identifier.")
    request_id: str | None = Field(default=None, max_length=100, description="Request correlation identifier.")
    ip_address: str | None = Field(default=None, max_length=45, description="Source IP address.")
    device_reference: str | None = Field(default=None, max_length=255, description="Device reference or identifier.")
    user_agent: str | None = Field(default=None, description="User agent string.")
    endpoint: str | None = Field(default=None, max_length=500, description="API endpoint or action reference.")
    old_value: str | None = Field(default=None, description="Previous value metadata in JSON-compatible form.")
    new_value: str | None = Field(default=None, description="New value metadata in JSON-compatible form.")
    change_summary: str | None = Field(default=None, description="Concise summary of the change.")
    metadata_payload: str | None = Field(default=None, description="Optional additional metadata without secrets.")


class AuditLogResponse(AuditLogBase):
    """Safe response schema for audit log records."""

    id: UUID = Field(..., description="Audit log identifier.")
    actor_type: str = Field(..., max_length=50, description="Type of actor that performed the action.")
    actor_id: str | None = Field(default=None, max_length=100, description="Reference to the actor, such as a user or admin identifier.")
    resource_id: str | None = Field(default=None, max_length=255, description="Affected resource identifier.")
    request_id: str | None = Field(default=None, max_length=100, description="Request correlation identifier.")
    created_at: datetime = Field(..., description="Audit log creation timestamp.")


class AuditLogFilterSchema(BaseModel):
    """Schema for filtering audit log records."""

    model_config = ConfigDict(from_attributes=True)

    action: str | None = Field(default=None, max_length=100, description="Filter by action name.")
    category: str | None = Field(default=None, max_length=100, description="Filter by audit category.")
    actor: str | None = Field(default=None, max_length=100, description="Filter by actor reference.")
    resource: str | None = Field(default=None, max_length=255, description="Filter by resource identifier.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date for the audit range.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date for the audit range.")
