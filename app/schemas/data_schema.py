from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataPurchaseSchema(BaseModel):
    """Schema for purchasing a mobile data bundle."""

    model_config = ConfigDict(from_attributes=True)

    phone_number: str = Field(..., min_length=10, max_length=15, description="Recipient phone number.")
    network: str = Field(..., max_length=100, description="Mobile network provider.")
    bundle_code: str = Field(..., max_length=100, description="Provider bundle code.")
    bundle_size: str | None = Field(default=None, max_length=100, description="Display size of the bundle.")
    amount: float = Field(..., gt=0, description="Bundle price.")

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.isdigit() or len(normalized) < 10:
            raise ValueError("phone_number must contain only digits and be at least 10 characters long")
        return normalized


class DataBundleResponse(BaseModel):
    """Response schema for a data bundle option."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Bundle identifier.")
    name: str | None = Field(default=None, max_length=255, description="Bundle name.")
    size: str | None = Field(default=None, max_length=100, description="Bundle size.")
    validity_period: str | None = Field(default=None, max_length=100, description="Bundle validity period.")
    price: float | None = Field(default=None, description="Bundle price.")
    network: str | None = Field(default=None, max_length=100, description="Supported network.")


class DataPurchaseResponse(BaseModel):
    """Response schema for a completed or pending data purchase."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    bundle: DataBundleResponse | None = Field(default=None, description="Selected bundle details.")
    amount: float | None = Field(default=None, description="Purchased amount.")
    status: str | None = Field(default=None, max_length=50, description="Purchase status.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class DataProviderResponse(BaseModel):
    """Schema for providers that support mobile data bundles."""

    model_config = ConfigDict(from_attributes=True)

    provider_name: str = Field(..., max_length=100, description="Provider name.")
    supported_networks: list[str] | None = Field(default=None, description="Networks supported by the provider.")
    status: str | None = Field(default=None, max_length=50, description="Provider availability status.")


class DataTransactionFilterSchema(BaseModel):
    """Schema for filtering data transactions."""

    model_config = ConfigDict(from_attributes=True)

    network: str | None = Field(default=None, max_length=100, description="Network filter.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
