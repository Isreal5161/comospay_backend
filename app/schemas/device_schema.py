from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeviceBase(BaseModel):
    """Shared device fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    device_name: str | None = Field(default=None, max_length=255, description="Device display name.")
    device_type: str | None = Field(default=None, max_length=100, description="Device type.")
    operating_system: str | None = Field(default=None, max_length=100, description="Operating system.")
    os_version: str | None = Field(default=None, max_length=100, description="Operating system version.")
    app_version: str | None = Field(default=None, max_length=100, description="Application version.")
    browser_name: str | None = Field(default=None, max_length=100, description="Browser name.")
    browser_version: str | None = Field(default=None, max_length=100, description="Browser version.")
    device_fingerprint: str | None = Field(default=None, max_length=255, description="Device fingerprint reference.")
    ip_address: str | None = Field(default=None, max_length=45, description="Last known IP address.")
    location_country: str | None = Field(default=None, max_length=100, description="Country metadata.")
    location_region: str | None = Field(default=None, max_length=100, description="Region metadata.")
    location_city: str | None = Field(default=None, max_length=100, description="City metadata.")


class DeviceCreate(DeviceBase):
    """Schema for registering a new device."""

    device_name: str | None = Field(default=None, max_length=255, description="Device display name.")
    device_type: str | None = Field(default=None, max_length=100, description="Device type.")
    operating_system: str | None = Field(default=None, max_length=100, description="Operating system.")
    os_version: str | None = Field(default=None, max_length=100, description="Operating system version.")
    app_version: str | None = Field(default=None, max_length=100, description="Application version.")
    browser_name: str | None = Field(default=None, max_length=100, description="Browser name.")
    browser_version: str | None = Field(default=None, max_length=100, description="Browser version.")
    device_fingerprint: str | None = Field(default=None, max_length=255, description="Device fingerprint reference.")
    ip_address: str | None = Field(default=None, max_length=45, description="Last known IP address.")
    location_country: str | None = Field(default=None, max_length=100, description="Country metadata.")
    location_region: str | None = Field(default=None, max_length=100, description="Region metadata.")
    location_city: str | None = Field(default=None, max_length=100, description="City metadata.")


class DeviceUpdate(BaseModel):
    """Schema for updating device metadata."""

    model_config = ConfigDict(from_attributes=True)

    device_name: str | None = Field(default=None, max_length=255, description="Device display name.")
    is_trusted: bool | None = Field(default=None, description="Whether the device is trusted.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class DeviceResponse(DeviceBase):
    """Safe response schema for device records."""

    id: UUID = Field(..., description="Device identifier.")
    is_trusted: bool = Field(..., description="Whether the device is trusted.")
    is_active: bool = Field(..., description="Whether the device is active.")
    is_revoked: bool = Field(..., description="Whether the device is revoked.")
    last_seen_at: datetime | None = Field(default=None, description="Last activity timestamp.")
    created_at: datetime = Field(..., description="Device creation timestamp.")


class DeviceVerificationSchema(BaseModel):
    """Schema for device verification requests."""

    verification_reference: str | None = Field(default=None, max_length=255, description="Verification reference information.")
