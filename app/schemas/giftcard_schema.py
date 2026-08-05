from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GiftCardCreateSchema(BaseModel):
    """Schema for submitting a gift card for processing."""

    model_config = ConfigDict(from_attributes=True)

    card_type: str = Field(..., max_length=100, description="Gift card type.")
    brand: str = Field(..., max_length=100, description="Gift card brand.")
    country: str | None = Field(default=None, max_length=100, description="Card issuing country.")
    currency: str = Field(default="NGN", max_length=3, description="Currency code.")
    amount: float = Field(..., gt=0, description="Gift card face value.")
    image_reference: str | None = Field(default=None, max_length=255, description="Reference to uploaded card image.")


class GiftCardRateResponse(BaseModel):
    """Response schema for a gift card rate entry."""

    model_config = ConfigDict(from_attributes=True)

    card_type: str | None = Field(default=None, max_length=100, description="Gift card type.")
    brand: str | None = Field(default=None, max_length=100, description="Gift card brand.")
    country: str | None = Field(default=None, max_length=100, description="Card issuing country.")
    exchange_rate: float | None = Field(default=None, description="Exchange rate for the card.")
    is_active: bool | None = Field(default=None, description="Whether the rate is active.")


class GiftCardTransactionResponse(BaseModel):
    """Response schema for a gift card transaction."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Internal transaction reference.")
    card_type: str | None = Field(default=None, max_length=100, description="Gift card type.")
    brand: str | None = Field(default=None, max_length=100, description="Gift card brand.")
    valuation: float | None = Field(default=None, description="Estimated or final valuation.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status.")
    created_at: datetime | None = Field(default=None, description="Transaction creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Transaction update timestamp.")


class GiftCardVerificationSchema(BaseModel):
    """Schema for verifying a gift card submission."""

    model_config = ConfigDict(from_attributes=True)

    gift_card_reference: str = Field(..., max_length=255, description="Gift card reference.")
    card_information_reference: str | None = Field(default=None, max_length=255, description="Reference to card information payload.")


class GiftCardFilterSchema(BaseModel):
    """Schema for filtering gift card records."""

    model_config = ConfigDict(from_attributes=True)

    brand: str | None = Field(default=None, max_length=100, description="Brand filter.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status filter.")
    start_date: datetime | None = Field(default=None, description="Inclusive start date filter.")
    end_date: datetime | None = Field(default=None, description="Inclusive end date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
