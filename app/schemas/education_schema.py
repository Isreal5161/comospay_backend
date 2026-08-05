from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EducationPurchaseSchema(BaseModel):
    """Schema for purchasing an education PIN or voucher."""

    model_config = ConfigDict(from_attributes=True)

    examination_type: str = Field(..., max_length=100, description="Examination type such as WAEC, NECO, JAMB, or NABTEB.")
    provider: str = Field(..., max_length=100, description="Education service provider.")
    quantity: int = Field(..., ge=1, description="Number of PINs or vouchers to purchase.")
    amount: float = Field(..., gt=0, description="Purchase amount.")


class EducationProductResponse(BaseModel):
    """Response schema for an education product option."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Product identifier.")
    examination_type: str | None = Field(default=None, max_length=100, description="Examination type.")
    provider: str | None = Field(default=None, max_length=100, description="Education provider.")
    price: float | None = Field(default=None, description="Product price.")
    is_available: bool | None = Field(default=None, description="Whether the product is available.")


class EducationVerificationSchema(BaseModel):
    """Schema for verifying an education transaction or reference."""

    model_config = ConfigDict(from_attributes=True)

    examination_reference: str = Field(..., max_length=255, description="Reference or transaction identifier.")
    verification_info: str | None = Field(default=None, description="Additional verification details.")


class EducationPurchaseResponse(BaseModel):
    """Response schema for an education purchase transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    examination_type: str | None = Field(default=None, max_length=100, description="Examination type.")
    provider: str | None = Field(default=None, max_length=100, description="Education provider.")
    reference: str | None = Field(default=None, max_length=255, description="PIN or reference information where allowed.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class EducationTransactionFilterSchema(BaseModel):
    """Schema for filtering education transactions."""

    model_config = ConfigDict(from_attributes=True)

    provider: str | None = Field(default=None, max_length=100, description="Provider filter.")
    examination_type: str | None = Field(default=None, max_length=100, description="Examination type filter.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
