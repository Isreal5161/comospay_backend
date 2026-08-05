from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FlutterwaveResponse(BaseModel):
    """Base response envelope for Flutterwave API calls."""

    model_config = ConfigDict(extra="allow")

    status: str | None = Field(default=None, description="API status value.")
    message: str | None = Field(default=None, description="API message.")
    data: dict[str, Any] | None = Field(default=None, description="Primary payload data.")


class FlutterwaveErrorResponse(BaseModel):
    """Structured error payload returned by Flutterwave."""

    model_config = ConfigDict(extra="allow")

    status: str | None = Field(default=None, description="Error status value.")
    message: str | None = Field(default=None, description="Error message.")
    code: str | None = Field(default=None, description="Provider error code.")
    data: dict[str, Any] | None = Field(default=None, description="Provider error details.")


class PaginationMetadata(BaseModel):
    """Pagination metadata for list-style responses."""

    page: int | None = Field(default=None, description="Current page number.")
    page_size: int | None = Field(default=None, description="Number of items per page.")
    total: int | None = Field(default=None, description="Total number of items.")


class PaymentInitializationRequest(BaseModel):
    """Request payload for creating a payment initialization request."""

    tx_ref: str = Field(..., description="Client-generated transaction reference.")
    amount: float | int = Field(..., description="Payment amount.")
    currency: str = Field(default="NGN", description="Payment currency.")
    redirect_url: str | None = Field(default=None, description="Redirect URL after payment completion.")
    customer: dict[str, Any] | None = Field(default=None, description="Customer details.")
    meta: dict[str, Any] | None = Field(default=None, description="Additional payment metadata.")


class PaymentInitializationResponse(FlutterwaveResponse):
    """Response payload for payment initialization."""

    data: dict[str, Any] | None = Field(default=None, description="Payment initialization details.")


class VirtualAccountRequest(BaseModel):
    """Request payload for creating a virtual account."""

    bvn: str | None = Field(default=None, description="Bank verification number.")
    email: str | None = Field(default=None, description="Customer email.")
    phonenumber: str | None = Field(default=None, description="Customer phone number.")
    firstname: str | None = Field(default=None, description="Customer first name.")
    lastname: str | None = Field(default=None, description="Customer last name.")
    tx_ref: str | None = Field(default=None, description="Client transaction reference.")
    nickname: str | None = Field(default=None, description="Friendly account nickname.")
    meta: dict[str, Any] | None = Field(default=None, description="Additional metadata.")


class VirtualAccountResponse(FlutterwaveResponse):
    """Response payload for virtual account creation."""

    data: dict[str, Any] | None = Field(default=None, description="Virtual account details.")


class TransferRequest(BaseModel):
    """Request payload for a bank transfer."""

    account_bank: str = Field(..., description="Destination bank code.")
    account_number: str = Field(..., description="Destination account number.")
    amount: float | int = Field(..., description="Transfer amount.")
    narration: str | None = Field(default=None, description="Transfer narration.")
    reference: str | None = Field(default=None, description="Client transfer reference.")
    currency: str = Field(default="NGN", description="Transfer currency.")
    debit_currency: str | None = Field(default=None, description="Currency to debit.")


class TransferResponse(FlutterwaveResponse):
    """Response payload for transfer operations."""

    data: dict[str, Any] | None = Field(default=None, description="Transfer details.")


class Bank(BaseModel):
    """Representation of a supported bank."""

    model_config = ConfigDict(extra="allow")

    id: int | None = Field(default=None, description="Bank identifier.")
    name: str | None = Field(default=None, description="Bank name.")
    code: str | None = Field(default=None, description="Bank code.")
    slug: str | None = Field(default=None, description="Bank slug.")


class BankListResponse(FlutterwaveResponse):
    """Response payload containing a list of supported banks."""

    data: list[Bank] | None = Field(default=None, description="List of supported banks.")
    pagination: PaginationMetadata | None = Field(default=None, description="Pagination metadata.")


class AccountResolutionRequest(BaseModel):
    """Request payload for resolving a bank account."""

    account_number: str = Field(..., description="Bank account number.")
    account_bank: str = Field(..., description="Bank code.")


class AccountResolutionResponse(FlutterwaveResponse):
    """Response payload for account resolution."""

    data: dict[str, Any] | None = Field(default=None, description="Resolved account details.")


class WebhookEvent(BaseModel):
    """Representation of a Flutterwave webhook event."""

    model_config = ConfigDict(extra="allow")

    event: str | None = Field(default=None, description="Webhook event name.")
    data: dict[str, Any] | None = Field(default=None, description="Webhook event payload.")


class WebhookPayload(BaseModel):
    """Complete payload sent by Flutterwave for webhook callbacks."""

    model_config = ConfigDict(extra="allow")

    event: str | None = Field(default=None, description="Event type.")
    data: dict[str, Any] | None = Field(default=None, description="Webhook payload data.")
    id: str | None = Field(default=None, description="Webhook identifier.")
    created_at: str | None = Field(default=None, description="Webhook creation timestamp.")
    event_type: str | None = Field(default=None, description="Alternative event type field.")
