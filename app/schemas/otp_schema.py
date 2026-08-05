from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OTPBase(BaseModel):
    """Shared OTP fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    purpose: str | None = Field(default=None, max_length=100, description="OTP purpose or use case.")
    delivery_method: str | None = Field(default=None, max_length=50, description="How the OTP is delivered.")


class OTPRequestSchema(OTPBase):
    """Schema for requesting an OTP."""

    destination: str = Field(..., max_length=255, description="Destination for the OTP, such as email or phone.")
    purpose: str = Field(..., max_length=100, description="OTP purpose or use case.")
    delivery_method: str | None = Field(default=None, max_length=50, description="How the OTP is delivered.")


class OTPVerifySchema(BaseModel):
    """Schema for verifying an OTP."""

    model_config = ConfigDict(from_attributes=True)

    otp_code: str = Field(..., min_length=4, max_length=10, description="OTP code supplied by the user.")
    verification_reference: str | None = Field(default=None, max_length=255, description="Reference for the OTP verification request.")


class OTPResponse(OTPBase):
    """Safe response schema for OTP records."""

    id: UUID = Field(..., description="OTP record identifier.")
    purpose: str = Field(..., max_length=100, description="OTP purpose or use case.")
    is_used: bool = Field(..., description="Whether the OTP has been used.")
    is_active: bool = Field(..., description="Whether the OTP is still active.")
    expires_at: datetime = Field(..., description="OTP expiration timestamp.")
    created_at: datetime = Field(..., description="OTP creation timestamp.")
