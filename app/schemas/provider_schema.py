from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProviderBase(BaseModel):
    """Shared provider fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=3, max_length=255, description="Provider name.")
    code: str | None = Field(default=None, min_length=2, max_length=100, description="Provider code.")
    category: str | None = Field(default=None, max_length=100, description="Provider category.")
    service_type: str | None = Field(default=None, max_length=100, description="Service type.")
    description: str | None = Field(default=None, description="Provider description.")
    status: str | None = Field(default=None, max_length=50, description="Provider status.")
    is_active: bool | None = Field(default=None, description="Whether the provider is active.")
    environment: str | None = Field(default=None, max_length=50, description="Provider environment.")
    priority: int | None = Field(default=None, ge=0, description="Provider priority.")


class ProviderCreate(ProviderBase):
    """Schema for creating a new provider configuration."""

    name: str = Field(..., min_length=3, max_length=255, description="Provider name.")
    code: str = Field(..., min_length=2, max_length=100, description="Provider code.")
    category: str = Field(..., max_length=100, description="Provider category.")
    service_type: str | None = Field(default=None, max_length=100, description="Service type.")
    description: str | None = Field(default=None, description="Provider description.")
    status: str | None = Field(default="active", max_length=50, description="Provider status.")
    is_active: bool | None = Field(default=True, description="Whether the provider is active.")
    environment: str | None = Field(default="production", max_length=50, description="Provider environment.")
    priority: int | None = Field(default=0, ge=0, description="Provider priority.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class ProviderUpdate(BaseModel):
    """Schema for updating provider information."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=3, max_length=255, description="Provider name.")
    code: str | None = Field(default=None, min_length=2, max_length=100, description="Provider code.")
    description: str | None = Field(default=None, description="Provider description.")
    status: str | None = Field(default=None, max_length=50, description="Provider status.")
    is_active: bool | None = Field(default=None, description="Whether the provider is active.")
    environment: str | None = Field(default=None, max_length=50, description="Provider environment.")
    priority: int | None = Field(default=None, ge=0, description="Provider priority.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class ProviderResponse(ProviderBase):
    """Safe provider response schema for API clients."""

    id: UUID = Field(..., description="Provider identifier.")
    created_at: datetime = Field(..., description="Provider creation timestamp.")
    updated_at: datetime = Field(..., description="Provider update timestamp.")


class ProviderStatusUpdateSchema(BaseModel):
    """Schema for provider activation and deactivation requests."""

    status: str = Field(..., max_length=50, description="Target provider status.")
    reason: str | None = Field(default=None, max_length=255, description="Optional reason for the status change.")
