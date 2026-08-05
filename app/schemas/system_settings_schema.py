from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SystemSettingsBase(BaseModel):
    """Shared system settings fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., max_length=255, description="Unique system setting key.")
    value: str | None = Field(default=None, description="Stored setting value.")
    type: str = Field(default="string", max_length=50, description="Setting value data type.")
    category: str = Field(default="general", max_length=100, description="Settings category.")
    description: str | None = Field(default=None, description="Human-readable description of the setting.")


class SystemSettingsCreate(SystemSettingsBase):
    """Schema for creating a new system setting."""

    key: str = Field(..., max_length=255, description="Unique system setting key.")
    value: str | None = Field(default=None, description="Stored setting value.")
    type: str = Field(default="string", max_length=50, description="Setting value data type.")
    category: str = Field(default="general", max_length=100, description="Settings category.")
    description: str | None = Field(default=None, description="Human-readable description of the setting.")


class SystemSettingsUpdate(BaseModel):
    """Schema for updating an existing system setting."""

    model_config = ConfigDict(from_attributes=True)

    value: str | None = Field(default=None, description="Updated setting value.")
    description: str | None = Field(default=None, description="Updated human-readable description.")
    is_active: bool | None = Field(default=None, description="Whether the setting is active.")


class SystemSettingsResponse(SystemSettingsBase):
    """Safe response schema for system setting records."""

    id: UUID = Field(..., description="System setting identifier.")
    key: str = Field(..., max_length=255, description="Unique system setting key.")
    value: str | None = Field(default=None, description="Stored setting value.")
    category: str = Field(..., max_length=100, description="Settings category.")
    type: str = Field(..., max_length=50, description="Setting value data type.")
    is_active: bool = Field(..., description="Whether the setting is active.")
    created_at: datetime = Field(..., description="Setting creation timestamp.")
    updated_at: datetime = Field(..., description="Setting update timestamp.")


class SystemSettingsToggleSchema(BaseModel):
    """Schema for enabling or disabling a system setting."""

    model_config = ConfigDict(from_attributes=True)

    is_active: bool = Field(..., description="Whether the setting should be enabled.")
    reason: str | None = Field(default=None, description="Optional reason for the state change.")
