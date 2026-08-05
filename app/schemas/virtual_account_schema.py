from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VirtualAccountBase(BaseModel):
    """Shared virtual account fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    provider: str | None = Field(default=None, max_length=100, description="Provider name.")
    account_number: str | None = Field(default=None, max_length=50, description="Provider-issued account number.")
    account_name: str | None = Field(default=None, max_length=255, description="Display name associated with the account.")
    bank_name: str | None = Field(default=None, max_length=255, description="Bank name associated with the account.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Provider reference for the account.")
    provider_customer_id: str | None = Field(default=None, max_length=255, description="Provider customer identifier.")
    provider_account_id: str | None = Field(default=None, max_length=255, description="Provider account identifier.")
    currency: str | None = Field(default=None, max_length=10, description="Currency of the virtual account.")
    status: str | None = Field(default=None, max_length=50, description="Lifecycle status of the virtual account.")
    kyc_status: str | None = Field(default=None, max_length=50, description="KYC verification status.")
    verification_status: str | None = Field(default=None, max_length=50, description="Provider verification status.")
    is_active: bool | None = Field(default=None, description="Whether the virtual account is active.")
    is_primary: bool | None = Field(default=None, description="Whether the virtual account is the primary account.")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional provider-specific metadata.")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        """Normalize provider names to a non-empty lowercase form."""
        if value is None:
            return value
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Provider name is required.")
        return normalized

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        """Normalize currency codes to uppercase ISO format."""
        if value is None:
            return value
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a valid ISO code.")
        return normalized


class VirtualAccountCreate(VirtualAccountBase):
    """Schema for creating a virtual account request."""

    wallet_id: UUID = Field(..., description="Wallet identifier that owns the virtual account.")
    user_id: UUID | None = Field(default=None, description="User identifier associated with the wallet.")
    provider: str = Field(default=..., max_length=100, description="Provider name.")
    account_number: str = Field(default=..., max_length=50, description="Provider-issued account number.")
    currency: str = Field(default=..., max_length=10, description="Currency of the virtual account.")


class VirtualAccountUpdate(VirtualAccountBase):
    """Schema for updating a virtual account."""


class VirtualAccountPrimaryUpdate(BaseModel):
    """Schema for changing the primary account flag."""

    is_primary: bool = Field(..., description="Whether the virtual account should be the primary account.")


class VirtualAccountStatusUpdate(BaseModel):
    """Schema for updating the account lifecycle status."""

    status: Literal["PENDING", "ACTIVE", "SUSPENDED", "CLOSED", "FAILED"] = Field(
        ..., description="Virtual account lifecycle status."
    )


class VirtualAccountKYCUpdate(BaseModel):
    """Schema for updating the virtual account KYC status."""

    kyc_status: Literal["NOT_STARTED", "PENDING", "VERIFIED", "REJECTED"] = Field(
        ..., description="KYC verification status."
    )


class VirtualAccountResponse(VirtualAccountBase):
    """Safe response schema for a single virtual account."""

    id: UUID = Field(..., description="Virtual account identifier.")
    wallet_id: UUID = Field(..., description="Wallet identifier that owns the virtual account.")
    user_id: UUID | None = Field(default=None, description="User identifier associated with the wallet.")
    provider: str = Field(default=..., max_length=100, description="Provider name.")
    account_number: str = Field(default=..., max_length=50, description="Provider-issued account number.")
    currency: str = Field(default=..., max_length=10, description="Currency of the virtual account.")
    status: str = Field(default=..., max_length=50, description="Lifecycle status of the virtual account.")
    kyc_status: str = Field(default=..., max_length=50, description="KYC verification status.")
    verification_status: str = Field(default=..., max_length=50, description="Provider verification status.")
    is_active: bool = Field(default=..., description="Whether the virtual account is active.")
    is_primary: bool = Field(default=..., description="Whether the virtual account is the primary account.")
    created_at: datetime = Field(..., description="Virtual account creation timestamp.")
    updated_at: datetime = Field(..., description="Virtual account update timestamp.")


class VirtualAccountListResponse(BaseModel):
    """Response schema for a paginated list of virtual accounts."""

    model_config = ConfigDict(from_attributes=True)

    items: list[VirtualAccountResponse] = Field(default_factory=list, description="Virtual accounts in the current page.")
    page: int = Field(..., ge=1, description="Current page number.")
    page_size: int = Field(..., ge=1, le=100, description="Number of results per page.")
    total: int = Field(..., ge=0, description="Total number of matching virtual accounts.")
    total_pages: int = Field(..., ge=0, description="Remaining number of pages.")


class VirtualAccountSummary(BaseModel):
    """Summary schema for virtual account analytics."""

    model_config = ConfigDict(from_attributes=True)

    total_accounts: int = Field(..., ge=0, description="Total number of virtual accounts.")
    active_accounts: int = Field(..., ge=0, description="Number of active virtual accounts.")
    primary_accounts: int = Field(..., ge=0, description="Number of primary virtual accounts.")
    providers: list[str] = Field(default_factory=list, description="Providers represented in the dataset.")


class VirtualAccountProviderDetails(BaseModel):
    """Provider detail subset for a virtual account response."""

    provider: str = Field(..., max_length=100, description="Provider name.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Provider reference.")
    provider_customer_id: str | None = Field(default=None, max_length=255, description="Provider customer identifier.")
    provider_account_id: str | None = Field(default=None, max_length=255, description="Provider account identifier.")


class VirtualAccountKYCInfo(BaseModel):
    """KYC-related subset for a virtual account response."""

    kyc_status: str = Field(..., max_length=50, description="KYC verification status.")
    verification_status: str = Field(..., max_length=50, description="Provider verification status.")
    is_active: bool = Field(..., description="Whether the virtual account is active.")
    is_primary: bool = Field(..., description="Whether the virtual account is the primary account.")


class VirtualAccountFilter(BaseModel):
    """Schema for filtering virtual account queries."""

    model_config = ConfigDict(from_attributes=True)

    wallet_id: UUID | None = Field(default=None, description="Wallet identifier filter.")
    user_id: UUID | None = Field(default=None, description="User identifier filter.")
    provider: str | None = Field(default=None, max_length=100, description="Provider filter.")
    status: str | None = Field(default=None, max_length=50, description="Status filter.")
    kyc_status: str | None = Field(default=None, max_length=50, description="KYC status filter.")
    verification_status: str | None = Field(default=None, max_length=50, description="Verification status filter.")
    is_active: bool | None = Field(default=None, description="Activity filter.")
    is_primary: bool | None = Field(default=None, description="Primary-account filter.")
    currency: str | None = Field(default=None, max_length=10, description="Currency filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of results per page.")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        """Normalize provider names when provided."""
        if value is None:
            return value
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Provider name is required.")
        return normalized

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        """Normalize currency codes when provided."""
        if value is None:
            return value
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a valid ISO code.")
        return normalized


class VirtualAccountSyncRequest(BaseModel):
    """Schema for provider synchronization requests."""

    provider: str = Field(..., max_length=100, description="Provider name.")
    wallet_id: UUID | None = Field(default=None, description="Wallet identifier to synchronize.")
    user_id: UUID | None = Field(default=None, description="User identifier to synchronize.")
    force_refresh: bool = Field(default=False, description="Whether to force refresh from the provider.")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        """Normalize provider names for synchronization requests."""
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Provider name is required.")
        return normalized


class VirtualAccountWebhookResponse(BaseModel):
    """Schema for provider callback or webhook responses."""

    provider: str = Field(..., max_length=100, description="Provider name.")
    event: str = Field(..., max_length=100, description="Webhook event name.")
    reference: str | None = Field(default=None, max_length=255, description="Provider reference.")
    status: str = Field(..., max_length=50, description="Webhook status.")
    payload: dict[str, Any] | None = Field(default=None, description="Provider payload.")
