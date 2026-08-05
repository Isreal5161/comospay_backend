from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LedgerBase(BaseModel):
    """Shared ledger fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    wallet_id: UUID | None = Field(default=None, description="Wallet identifier.")
    user_id: UUID | None = Field(default=None, description="User identifier.")
    related_transaction_id: UUID | None = Field(default=None, description="Related transaction identifier.")
    transaction_reference: str | None = Field(default=None, max_length=100, description="Business transaction reference.")
    transaction_type: str | None = Field(default=None, max_length=100, description="Transaction type.")
    entry_type: str | None = Field(default=None, max_length=50, description="Accounting entry type.")
    description: str | None = Field(default=None, description="Ledger description.")
    currency: str | None = Field(default=None, max_length=10, description="ISO currency code.")
    status: str | None = Field(default=None, max_length=50, description="Ledger status.")
    opening_balance: Decimal | None = Field(default=None, ge=0, description="Opening balance before the movement.")
    debit_amount: Decimal | None = Field(default=None, ge=0, description="Debit amount applied.")
    credit_amount: Decimal | None = Field(default=None, ge=0, description="Credit amount applied.")
    closing_balance: Decimal | None = Field(default=None, ge=0, description="Closing balance after the movement.")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata.")
    created_by: str | None = Field(default=None, max_length=100, description="Actor that created the ledger entry.")
    created_at: datetime | None = Field(default=None, description="Ledger creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Ledger update timestamp.")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        """Normalize and validate the currency value."""
        if value is None:
            return value

        normalized = value.strip().upper()
        if not normalized:
            return None

        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a valid ISO code such as NGN or USD.")

        return normalized


class LedgerCreate(LedgerBase):
    """Schema for creating a new ledger entry."""

    wallet_id: UUID = Field(..., description="Wallet identifier.")
    user_id: UUID = Field(..., description="User identifier.")
    transaction_reference: str = Field(..., max_length=100, description="Business transaction reference.")
    transaction_type: str = Field(..., max_length=100, description="Transaction type.")
    entry_type: str = Field(..., max_length=50, description="Accounting entry type.")
    currency: str = Field(..., max_length=10, description="ISO currency code.")


class LedgerUpdate(LedgerBase):
    """Schema for updating an existing ledger entry."""


class LedgerResponse(LedgerBase):
    """Safe response schema for ledger records."""

    id: UUID = Field(..., description="Ledger identifier.")
    wallet_id: UUID = Field(..., description="Wallet identifier.")
    user_id: UUID = Field(..., description="User identifier.")
    transaction_reference: str = Field(..., max_length=100, description="Business transaction reference.")
    transaction_type: str = Field(..., max_length=100, description="Transaction type.")
    entry_type: str = Field(..., max_length=50, description="Accounting entry type.")
    currency: str = Field(..., max_length=10, description="ISO currency code.")
    status: str = Field(..., max_length=50, description="Ledger status.")
    opening_balance: Decimal = Field(..., ge=0, description="Opening balance before the movement.")
    debit_amount: Decimal = Field(..., ge=0, description="Debit amount applied.")
    credit_amount: Decimal = Field(..., ge=0, description="Credit amount applied.")
    closing_balance: Decimal = Field(..., ge=0, description="Closing balance after the movement.")
    created_by: str = Field(..., max_length=100, description="Actor that created the ledger entry.")
    created_at: datetime = Field(..., description="Ledger creation timestamp.")
    updated_at: datetime = Field(..., description="Ledger update timestamp.")


class LedgerDetailResponse(LedgerResponse):
    """Detailed response schema for ledger records."""


class LedgerSummary(BaseModel):
    """Schema for ledger summary metrics."""

    model_config = ConfigDict(from_attributes=True)

    total_entries: int = Field(..., ge=0, description="Total number of ledger entries.")
    total_debit_amount: Decimal = Field(..., ge=0, description="Aggregate debit amount.")
    total_credit_amount: Decimal = Field(..., ge=0, description="Aggregate credit amount.")
    total_opening_balance: Decimal = Field(..., ge=0, description="Aggregate opening balance.")
    total_closing_balance: Decimal = Field(..., ge=0, description="Aggregate closing balance.")
    currency: str | None = Field(default=None, max_length=10, description="Currency used in the summary.")


class LedgerFilter(BaseModel):
    """Schema for filtering ledger records."""

    model_config = ConfigDict(from_attributes=True)

    wallet_id: UUID | None = Field(default=None, description="Wallet identifier filter.")
    user_id: UUID | None = Field(default=None, description="User identifier filter.")
    related_transaction_id: UUID | None = Field(default=None, description="Related transaction identifier filter.")
    transaction_reference: str | None = Field(default=None, max_length=100, description="Transaction reference filter.")
    transaction_type: str | None = Field(default=None, max_length=100, description="Transaction type filter.")
    entry_type: str | None = Field(default=None, max_length=50, description="Entry type filter.")
    currency: str | None = Field(default=None, max_length=10, description="Currency filter.")
    status: str | None = Field(default=None, max_length=50, description="Status filter.")
    created_by: str | None = Field(default=None, max_length=100, description="Created by filter.")
    start_date: datetime | None = Field(default=None, description="Start date filter.")
    end_date: datetime | None = Field(default=None, description="End date filter.")
    page: int | None = Field(default=1, ge=1, description="Page number.")
    page_size: int | None = Field(default=20, ge=1, le=100, description="Number of records per page.")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        """Normalize and validate the currency filter value."""
        if value is None:
            return value

        normalized = value.strip().upper()
        if not normalized:
            return None

        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a valid ISO code such as NGN or USD.")

        return normalized


class LedgerSearch(LedgerFilter):
    """Schema for search requests against ledger records."""

    query: str | None = Field(default=None, min_length=1, max_length=255, description="Free-text search query.")
    sort_by: str | None = Field(default=None, max_length=50, description="Field to sort by.")
    sort_order: Literal["asc", "desc"] | None = Field(default=None, description="Sort order.")


class LedgerStatistics(BaseModel):
    """Schema for ledger statistics payloads."""

    model_config = ConfigDict(from_attributes=True)

    total_entries: int = Field(..., ge=0, description="Total number of entries.")
    total_debit_amount: Decimal = Field(..., ge=0, description="Aggregate debit amount.")
    total_credit_amount: Decimal = Field(..., ge=0, description="Aggregate credit amount.")
    total_opening_balance: Decimal = Field(..., ge=0, description="Aggregate opening balance.")
    total_closing_balance: Decimal = Field(..., ge=0, description="Aggregate closing balance.")
    currency: str | None = Field(default=None, max_length=10, description="Currency used in the statistics.")
    status_breakdown: dict[str, int] | None = Field(default=None, description="Status distribution.")
    entry_type_breakdown: dict[str, int] | None = Field(default=None, description="Entry type distribution.")
    transaction_type_breakdown: dict[str, int] | None = Field(default=None, description="Transaction type distribution.")


class LedgerExportRequest(BaseModel):
    """Schema for ledger export requests."""

    model_config = ConfigDict(from_attributes=True)

    format: Literal["csv", "json", "xlsx"] = Field(default="csv", description="Export file format.")
    filters: LedgerFilter | None = Field(default=None, description="Optional filters for the export query.")
    include_metadata: bool = Field(default=False, description="Whether to include metadata in the exported payload.")


class LedgerExportResponse(BaseModel):
    """Schema for ledger export responses."""

    model_config = ConfigDict(from_attributes=True)

    download_url: str | None = Field(default=None, description="Download URL for the exported file.")
    filename: str | None = Field(default=None, max_length=255, description="Exported file name.")
    format: Literal["csv", "json", "xlsx"] = Field(..., description="Export file format.")
    record_count: int = Field(default=0, ge=0, description="Number of exported records.")
    generated_at: datetime | None = Field(default=None, description="Export generation timestamp.")
