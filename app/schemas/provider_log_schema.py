from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProviderLogBase(BaseModel):
    """Shared provider log fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    provider_name: str | None = Field(default=None, max_length=100, description="Provider name.")
    category: str | None = Field(default=None, max_length=100, description="Provider category.")
    service_name: str | None = Field(default=None, max_length=100, description="Service or action name.")
    action: str | None = Field(default=None, max_length=100, description="Provider action being executed.")
    status: str | None = Field(default=None, max_length=50, description="Provider interaction status.")
    success: bool | None = Field(default=None, description="Whether the interaction succeeded.")


class ProviderLogCreate(ProviderLogBase):
    """Schema for creating a provider communication log entry."""

    provider_name: str = Field(..., max_length=100, description="Provider name.")
    category: str | None = Field(default=None, max_length=100, description="Provider category.")
    service_name: str | None = Field(default=None, max_length=100, description="Service or action name.")
    action: str | None = Field(default=None, max_length=100, description="Provider action being executed.")
    reference: str | None = Field(default=None, max_length=255, description="Correlation reference for the provider interaction.")
    request_reference: str | None = Field(default=None, max_length=255, description="Reference for the originating request.")
    request_payload: str | None = Field(default=None, description="Sanitized request metadata payload.")
    response_payload: str | None = Field(default=None, description="Sanitized response metadata payload.")
    status: str | None = Field(default="pending", max_length=50, description="Provider interaction status.")
    response_code: str | None = Field(default=None, max_length=50, description="Provider response code.")
    success: bool = Field(default=False, description="Whether the interaction succeeded.")
    duration_ms: int | None = Field(default=None, ge=0, description="Elapsed response duration in milliseconds.")
    error_message: str | None = Field(default=None, description="Sanitized error detail if applicable.")


class ProviderLogResponse(ProviderLogBase):
    """Safe response schema for provider log records."""

    id: UUID = Field(..., description="Provider log identifier.")
    provider_name: str = Field(..., max_length=100, description="Provider name.")
    action: str | None = Field(default=None, max_length=100, description="Provider action being executed.")
    status: str = Field(..., max_length=50, description="Provider interaction status.")
    success: bool = Field(..., description="Whether the interaction succeeded.")
    duration_ms: int | None = Field(default=None, description="Elapsed response duration in milliseconds.")
    created_at: datetime = Field(..., description="Provider log creation timestamp.")
    updated_at: datetime = Field(..., description="Provider log update timestamp.")
