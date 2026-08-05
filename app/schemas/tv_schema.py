from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TVSubscriptionSchema(BaseModel):
    """Schema for initiating a TV subscription purchase."""

    model_config = ConfigDict(from_attributes=True)

    provider: str = Field(..., max_length=100, description="TV subscription provider.")
    service_type: str | None = Field(default=None, max_length=100, description="Service type or bouquet.")
    smart_card_number: str = Field(..., min_length=4, max_length=50, description="Smart card or IUC number.")
    package_code: str | None = Field(default=None, max_length=100, description="Package code.")
    amount: float = Field(..., gt=0, description="Subscription amount.")


class TVPackageResponse(BaseModel):
    """Response schema for a TV package option."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Package identifier.")
    package_name: str | None = Field(default=None, max_length=255, description="Package name.")
    provider: str | None = Field(default=None, max_length=100, description="Provider name.")
    price: float | None = Field(default=None, description="Package price.")
    duration: str | None = Field(default=None, max_length=100, description="Package duration.")


class SmartCardVerificationSchema(BaseModel):
    """Schema for verifying a smart card before subscription."""

    model_config = ConfigDict(from_attributes=True)

    smart_card_number: str = Field(..., min_length=4, max_length=50, description="Smart card or IUC number.")
    provider: str = Field(..., max_length=100, description="TV subscription provider.")


class SmartCardVerificationResponse(BaseModel):
    """Safe response schema for smart card verification."""

    model_config = ConfigDict(from_attributes=True)

    customer_name: str | None = Field(default=None, max_length=255, description="Customer display name.")
    smart_card_reference: str | None = Field(default=None, max_length=50, description="Masked smart card reference.")
    subscription_status: str | None = Field(default=None, max_length=50, description="Current subscription status.")


class TVSubscriptionResponse(BaseModel):
    """Response schema for a TV subscription transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    provider: str | None = Field(default=None, max_length=100, description="TV subscription provider.")
    package: TVPackageResponse | None = Field(default=None, description="Selected package details.")
    amount: float | None = Field(default=None, description="Subscription amount.")
    status: str | None = Field(default=None, max_length=50, description="Subscription status.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class TVTransactionFilterSchema(BaseModel):
    """Schema for filtering TV subscription transactions."""

    model_config = ConfigDict(from_attributes=True)

    provider: str | None = Field(default=None, max_length=100, description="Provider filter.")
    status: str | None = Field(default=None, max_length=50, description="Subscription status filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
