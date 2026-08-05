from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TransactionBase(BaseModel):
    """Shared transaction fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    transaction_type: str | None = Field(default=None, max_length=100, description="Transaction type.")
    category: str | None = Field(default=None, max_length=100, description="Transaction category.")
    amount: Decimal | None = Field(default=None, gt=0, description="Transaction amount.")
    currency: str | None = Field(default=None, max_length=10, description="Transaction currency.")
    description: str | None = Field(default=None, description="Transaction description.")


class TransactionCreate(TransactionBase):
    """Schema for creating a new transaction request."""

    amount: Decimal = Field(..., gt=0, description="Transaction amount.")
    transaction_type: str = Field(..., max_length=100, description="Transaction type.")
    category: str = Field(..., max_length=100, description="Transaction category.")
    reference: str | None = Field(default=None, max_length=255, description="Client reference.")
    provider_name: str | None = Field(default=None, max_length=100, description="Provider name.")
    external_reference: str | None = Field(default=None, max_length=255, description="External reference.")


class TransactionResponse(TransactionBase):
    """Safe response schema for transaction records."""

    id: UUID = Field(..., description="Transaction identifier.")
    reference: str = Field(..., max_length=100, description="Transaction reference.")
    amount: Decimal = Field(..., gt=0, description="Transaction amount.")
    charges: Decimal = Field(..., description="Transaction charges.")
    total_amount: Decimal = Field(..., description="Total amount including charges.")
    currency: str = Field(..., max_length=10, description="Transaction currency.")
    status: str = Field(..., max_length=50, description="Transaction status.")
    created_at: datetime = Field(..., description="Transaction creation timestamp.")
    updated_at: datetime = Field(..., description="Transaction update timestamp.")


class TransactionDetailResponse(TransactionResponse):
    """Detailed transaction response schema."""

    provider_name: str | None = Field(default=None, max_length=100, description="Provider name.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Provider reference.")
    provider_transaction_id: str | None = Field(default=None, max_length=255, description="Provider transaction identifier.")
    wallet_id: UUID | None = Field(default=None, description="Wallet identifier.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata.")


class TransactionStatusUpdateSchema(BaseModel):
    """Schema for transaction status update requests."""

    status: str = Field(..., max_length=50, description="Transaction status.")
    reason: str | None = Field(default=None, max_length=255, description="Reason for the status change.")


class TransactionFilterSchema(BaseModel):
    """Schema for filtering transaction history."""

    transaction_type: str | None = Field(default=None, max_length=100, description="Transaction type filter.")
    status: str | None = Field(default=None, max_length=50, description="Transaction status filter.")
    start_date: datetime | None = Field(default=None, description="Start date filter.")
    end_date: datetime | None = Field(default=None, description="End date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")
