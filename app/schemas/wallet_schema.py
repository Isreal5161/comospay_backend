from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WalletBase(BaseModel):
    """Shared wallet fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    wallet_type: str | None = Field(default=None, max_length=50, description="Wallet type.")
    currency: str | None = Field(default=None, max_length=10, description="Wallet currency.")
    status: str | None = Field(default=None, max_length=50, description="Wallet status.")


class WalletCreate(WalletBase):
    """Schema for creating a new wallet."""

    wallet_type: str = Field(..., max_length=50, description="Wallet type.")
    currency: str = Field(..., max_length=10, description="Wallet currency.")


class WalletResponse(WalletBase):
    """Safe response schema for wallet records."""

    id: UUID = Field(..., description="Wallet identifier.")
    wallet_reference: str | None = Field(default=None, max_length=100, description="Wallet reference.")
    available_balance: Decimal = Field(..., description="Available balance.")
    ledger_balance: Decimal = Field(..., description="Ledger balance.")
    locked_balance: Decimal = Field(..., description="Locked balance.")
    created_at: datetime = Field(..., description="Wallet creation timestamp.")
    updated_at: datetime = Field(..., description="Wallet update timestamp.")


class WalletBalanceResponse(BaseModel):
    """Schema for wallet balance queries."""

    available_balance: Decimal = Field(..., description="Available balance.")
    currency: str = Field(..., max_length=10, description="Wallet currency.")


class WalletFreezeSchema(BaseModel):
    """Schema for wallet freeze requests."""

    reason: str | None = Field(default=None, max_length=255, description="Reason for freezing the wallet.")


class WalletTransferSchema(BaseModel):
    """Schema for transfer requests."""

    destination_reference: str = Field(..., max_length=255, description="Destination wallet or account reference.")
    amount: Decimal = Field(..., gt=0, description="Transfer amount.")
    description: str | None = Field(default=None, max_length=255, description="Transfer description.")
