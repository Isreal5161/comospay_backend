from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """Shared user fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str | None = Field(default=None, max_length=100, description="User first name.")
    last_name: str | None = Field(default=None, max_length=100, description="User last name.")
    email: EmailStr | None = Field(default=None, description="User email address.")
    phone: str | None = Field(default=None, max_length=20, description="User phone number.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Optional username.")
    profile_image_url: str | None = Field(default=None, max_length=500, description="Profile image URL.")
    status: str | None = Field(default=None, max_length=50, description="Account status.")
    is_active: bool | None = Field(default=None, description="Whether the account is active.")
    email_verified: bool | None = Field(default=None, description="Whether the email is verified.")
    phone_verified: bool | None = Field(default=None, description="Whether the phone number is verified.")


class UserCreate(UserBase):
    """Schema for creating a new user account."""

    first_name: str | None = Field(default=None, max_length=100, description="User first name.")
    last_name: str | None = Field(default=None, max_length=100, description="User last name.")
    email: EmailStr = Field(..., description="User email address.")
    phone: str | None = Field(default=None, max_length=20, description="User phone number.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Optional username.")
    password: str = Field(..., min_length=8, max_length=255, description="Raw password.")


class UserUpdate(BaseModel):
    """Schema for updating user profile and contact information."""

    model_config = ConfigDict(from_attributes=True)

    first_name: str | None = Field(default=None, max_length=100, description="User first name.")
    last_name: str | None = Field(default=None, max_length=100, description="User last name.")
    email: EmailStr | None = Field(default=None, description="User email address.")
    phone: str | None = Field(default=None, max_length=20, description="User phone number.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Optional username.")
    profile_image_url: str | None = Field(default=None, max_length=500, description="Profile image URL.")


class UserResponse(UserBase):
    """Safe public response schema for user records."""

    id: UUID = Field(..., description="User identifier.")
    created_at: datetime = Field(..., description="Account creation timestamp.")
    updated_at: datetime = Field(..., description="Account update timestamp.")
    last_login_at: datetime | None = Field(default=None, description="Last login timestamp.")


class UserLoginSchema(BaseModel):
    """Schema for user authentication requests."""

    identifier: str = Field(..., min_length=3, max_length=255, description="Email or phone identifier.")
    password: str = Field(..., min_length=8, max_length=255, description="User password.")


class PasswordChangeSchema(BaseModel):
    """Schema for password change requests."""

    current_password: str = Field(..., min_length=8, max_length=255, description="Current password.")
    new_password: str = Field(..., min_length=8, max_length=255, description="New password.")


class UserStatusUpdateSchema(BaseModel):
    """Schema for admin status update requests."""

    status: str = Field(..., max_length=50, description="User account status.")
    reason: str | None = Field(default=None, max_length=255, description="Reason for the status change.")
