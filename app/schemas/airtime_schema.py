from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AirtimePurchaseSchema(BaseModel):
    """Schema for purchasing airtime."""

    model_config = ConfigDict(from_attributes=True)

    phone_number: str = Field(..., min_length=10, max_length=15, description="Recipient phone number.")
    network: str = Field(..., max_length=100, description="Mobile network provider.")
    amount: float = Field(..., gt=0, description="Airtime amount to purchase.")
    currency: str = Field(default="NGN", max_length=3, description="Currency code.")

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.isdigit() or len(normalized) < 10:
            raise ValueError("phone_number must contain only digits and be at least 10 characters long")
        return normalized


class AirtimeResponse(BaseModel):
    """Safe response schema for an airtime purchase."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    phone_number: str | None = Field(default=None, max_length=15, description="Masked recipient phone number.")
    network: str | None = Field(default=None, max_length=100, description="Mobile network provider.")
    amount: float | None = Field(default=None, description="Purchased airtime amount.")
    status: str | None = Field(default=None, max_length=50, description="Purchase status.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class AirtimeProviderResponse(BaseModel):
    """Schema for available airtime providers."""

    model_config = ConfigDict(from_attributes=True)

    provider_name: str = Field(..., max_length=100, description="Provider name.")
    network: str | None = Field(default=None, max_length=100, description="Supported network name.")
    is_available: bool = Field(..., description="Whether the provider is currently available.")


class AirtimeTransactionFilterSchema(BaseModel):
    """Schema for filtering airtime transactions."""

    model_config = ConfigDict(from_attributes=True)

    status: str | None = Field(default=None, max_length=50, description="Transaction status filter.")
    network: str | None = Field(default=None, max_length=100, description="Network filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
