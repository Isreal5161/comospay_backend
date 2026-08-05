from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ElectricityPaymentSchema(BaseModel):
    """Schema for initiating an electricity bill payment."""

    model_config = ConfigDict(from_attributes=True)

    provider: str = Field(..., max_length=100, description="Electricity provider name.")
    disco: str = Field(..., max_length=100, description="Distribution company or disco.")
    meter_number: str = Field(..., min_length=4, max_length=50, description="Customer meter number.")
    meter_type: str | None = Field(default=None, max_length=50, description="Meter type.")
    customer_name: str | None = Field(default=None, max_length=255, description="Customer display name.")
    amount: float = Field(..., gt=0, description="Payment amount.")


class MeterVerificationSchema(BaseModel):
    """Schema for verifying a meter before payment."""

    model_config = ConfigDict(from_attributes=True)

    meter_number: str = Field(..., min_length=4, max_length=50, description="Customer meter number.")
    disco: str = Field(..., max_length=100, description="Distribution company or disco.")
    meter_type: str | None = Field(default=None, max_length=50, description="Meter type.")


class MeterVerificationResponse(BaseModel):
    """Safe response schema for meter verification."""

    model_config = ConfigDict(from_attributes=True)

    customer_name: str | None = Field(default=None, max_length=255, description="Customer display name.")
    meter_number: str | None = Field(default=None, max_length=50, description="Masked meter number.")
    meter_type: str | None = Field(default=None, max_length=50, description="Meter type.")
    address: str | None = Field(default=None, description="Customer address or reference information.")
    reference: str | None = Field(default=None, max_length=255, description="Verification reference.")


class ElectricityPaymentResponse(BaseModel):
    """Response schema for an electricity payment transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    amount: float | None = Field(default=None, description="Payment amount.")
    provider: str | None = Field(default=None, max_length=100, description="Electricity provider name.")
    status: str | None = Field(default=None, max_length=50, description="Payment status.")
    token: str | None = Field(default=None, max_length=255, description="Token or reference returned by the provider.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class ElectricityFilterSchema(BaseModel):
    """Schema for filtering electricity payment records."""

    model_config = ConfigDict(from_attributes=True)

    status: str | None = Field(default=None, max_length=50, description="Payment status filter.")
    provider: str | None = Field(default=None, max_length=100, description="Provider filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
