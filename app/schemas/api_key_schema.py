from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class APIKeyBase(BaseModel):
    """Shared API key fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=3, max_length=255, description="API key name.")
    description: str | None = Field(default=None, description="API key description.")
    environment: str | None = Field(default=None, max_length=50, description="Deployment environment.")
    status: str | None = Field(default=None, max_length=50, description="API key status.")
    owner_type: str | None = Field(default=None, max_length=50, description="Owner type.")
    owner_id: str | None = Field(default=None, max_length=100, description="Owner identifier.")
    is_active: bool | None = Field(default=None, description="Whether the API key is active.")
    tags: str | None = Field(default=None, description="Optional tags for categorization.")


class APIKeyCreate(APIKeyBase):
    """Schema for creating a new API key."""

    name: str = Field(..., min_length=3, max_length=255, description="API key name.")
    environment: str | None = Field(default="production", max_length=50, description="Deployment environment.")
    description: str | None = Field(default=None, description="API key description.")
    owner_type: str | None = Field(default=None, max_length=50, description="Owner type.")
    owner_id: str | None = Field(default=None, max_length=100, description="Owner identifier.")


class APIKeyUpdate(BaseModel):
    """Schema for updating API key metadata."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=3, max_length=255, description="API key name.")
    description: str | None = Field(default=None, description="API key description.")
    status: str | None = Field(default=None, max_length=50, description="API key status.")
    is_active: bool | None = Field(default=None, description="Whether the API key is active.")
    expires_at: datetime | None = Field(default=None, description="API key expiration timestamp.")


class APIKeyResponse(APIKeyBase):
    """Safe API response schema for API key records."""

    id: UUID = Field(..., description="API key identifier.")
    created_at: datetime = Field(..., description="API key creation timestamp.")
    updated_at: datetime = Field(..., description="API key update timestamp.")
    expires_at: datetime | None = Field(default=None, description="API key expiration timestamp.")
    last_used_at: datetime | None = Field(default=None, description="Last used timestamp.")
    revoked_at: datetime | None = Field(default=None, description="Revocation timestamp.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class APIKeyRevokeSchema(BaseModel):
    """Schema for API key revocation requests."""

    reason: str | None = Field(default=None, max_length=255, description="Revocation reason.")
