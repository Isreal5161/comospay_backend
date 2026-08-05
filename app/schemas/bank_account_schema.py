from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BankAccountBase(BaseModel):
    """Shared bank account fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    bank_name: str | None = Field(default=None, max_length=255, description="Bank name.")
    account_name: str | None = Field(default=None, max_length=255, description="Account holder name.")
    account_number: str | None = Field(default=None, max_length=50, description="Account number reference.")
    bank_code: str | None = Field(default=None, max_length=50, description="Bank code.")
    account_type: str | None = Field(default=None, max_length=50, description="Account type.")
    is_default: bool | None = Field(default=None, description="Whether the account is the default account.")
    status: str | None = Field(default=None, max_length=50, description="Account status.")
    provider_name: str | None = Field(default=None, max_length=100, description="Provider name.")
    virtual_account_number: str | None = Field(default=None, max_length=50, description="Virtual account number.")
    virtual_bank_name: str | None = Field(default=None, max_length=255, description="Virtual bank name.")


class BankAccountCreate(BankAccountBase):
    """Schema for adding a new bank or virtual account."""

    bank_name: str | None = Field(default=None, max_length=255, description="Bank name.")
    account_name: str | None = Field(default=None, max_length=255, description="Account holder name.")
    account_number: str | None = Field(default=None, max_length=50, description="Account number reference.")
    bank_code: str | None = Field(default=None, max_length=50, description="Bank code.")
    account_type: str | None = Field(default=None, max_length=50, description="Account type.")
    is_default: bool | None = Field(default=False, description="Whether the account is the default account.")
    status: str | None = Field(default="pending", max_length=50, description="Account status.")
    provider_name: str | None = Field(default=None, max_length=100, description="Provider name.")
    virtual_account_number: str | None = Field(default=None, max_length=50, description="Virtual account number.")
    virtual_bank_name: str | None = Field(default=None, max_length=255, description="Virtual bank name.")


class BankAccountUpdate(BaseModel):
    """Schema for updating bank account metadata."""

    model_config = ConfigDict(from_attributes=True)

    account_name: str | None = Field(default=None, max_length=255, description="Account holder name.")
    status: str | None = Field(default=None, max_length=50, description="Account status.")
    is_default: bool | None = Field(default=None, description="Whether the account is the default account.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class BankAccountResponse(BankAccountBase):
    """Safe response schema for bank account records."""

    id: UUID = Field(..., description="Bank account identifier.")
    masked_account_number: str | None = Field(default=None, description="Masked account number.")
    is_active: bool = Field(..., description="Whether the account is active.")
    status: str = Field(..., max_length=50, description="Account status.")
    created_at: datetime = Field(..., description="Account creation timestamp.")
    updated_at: datetime = Field(..., description="Account update timestamp.")
    verified_at: datetime | None = Field(default=None, description="Verification timestamp.")


class BankAccountVerificationSchema(BaseModel):
    """Schema for bank account verification requests."""

    verification_reference: str | None = Field(default=None, max_length=255, description="Verification reference.")
