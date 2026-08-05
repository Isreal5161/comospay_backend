from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AdminBase(BaseModel):
    """Shared admin profile fields for API validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr | None = Field(default=None, description="Admin email address.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Admin username.")
    role: str | None = Field(default=None, max_length=50, description="Admin role.")
    status: str | None = Field(default=None, max_length=50, description="Admin account status.")
    is_active: bool | None = Field(default=None, description="Whether the admin account is active.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")


class AdminCreate(AdminBase):
    """Schema for creating a new admin account."""

    email: EmailStr = Field(..., description="Admin email address.")
    username: str = Field(..., min_length=3, max_length=100, description="Admin username.")
    password: str = Field(..., min_length=8, max_length=255, description="Raw admin password.")
    role: str | None = Field(default="admin", max_length=50, description="Admin role.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")


class AdminUpdate(BaseModel):
    """Schema for updating admin profile and account metadata."""

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr | None = Field(default=None, description="Admin email address.")
    username: str | None = Field(default=None, min_length=3, max_length=100, description="Admin username.")
    first_name: str | None = Field(default=None, max_length=100, description="Admin first name.")
    last_name: str | None = Field(default=None, max_length=100, description="Admin last name.")
    phone: str | None = Field(default=None, max_length=20, description="Admin phone number.")
    role: str | None = Field(default=None, max_length=50, description="Admin role.")
    status: str | None = Field(default=None, max_length=50, description="Admin account status.")
    is_active: bool | None = Field(default=None, description="Whether the admin account is active.")


class AdminResponse(AdminBase):
    """Safe public response schema for admin records."""

    id: UUID = Field(..., description="Admin identifier.")
    created_at: datetime = Field(..., description="Admin creation timestamp.")
    updated_at: datetime = Field(..., description="Admin update timestamp.")


class AdminLoginSchema(BaseModel):
    """Schema for admin authentication requests."""

    login_identifier: str = Field(..., min_length=3, max_length=255, description="Email or username used for login.")
    password: str = Field(..., min_length=8, max_length=255, description="Admin password.")


class AdminPasswordChangeSchema(BaseModel):
    """Schema for admin password change requests."""

    current_password: str = Field(..., min_length=8, max_length=255, description="Current password.")
    new_password: str = Field(..., min_length=8, max_length=255, description="New password.")
