from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PaymentInitializeSchema(BaseModel):
    """Schema for initiating a new payment."""

    model_config = ConfigDict(from_attributes=True)

    amount: float = Field(..., gt=0, description="Payment amount.")
    currency: str = Field(default="NGN", max_length=3, description="Payment currency code.")
    payment_method: str | None = Field(default=None, max_length=100, description="Payment method or channel.")
    description: str | None = Field(default=None, description="Payment description.")
    callback_reference: str | None = Field(default=None, max_length=255, description="Callback or reference supplied by the caller.")


class PaymentVerifySchema(BaseModel):
    """Schema for verifying a completed payment."""

    model_config = ConfigDict(from_attributes=True)

    transaction_reference: str = Field(..., max_length=255, description="Transaction reference for the payment.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Provider-assigned reference.")


class PaymentResponse(BaseModel):
    """Safe response schema for a payment operation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(default=None, description="Payment identifier.")
    payment_reference: str | None = Field(default=None, max_length=255, description="Internal payment reference.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Transaction reference.")
    amount: float | None = Field(default=None, description="Payment amount.")
    currency: str | None = Field(default=None, max_length=3, description="Payment currency code.")
    status: str | None = Field(default=None, max_length=50, description="Payment status.")
    provider: str | None = Field(default=None, max_length=100, description="Provider name.")
    created_at: datetime | None = Field(default=None, description="Payment creation timestamp.")
    updated_at: datetime | None = Field(default=None, description="Payment update timestamp.")


class PaymentWebhookSchema(BaseModel):
    """Schema for provider webhook payloads received by the platform."""

    model_config = ConfigDict(from_attributes=True)

    event_type: str | None = Field(default=None, max_length=100, description="Provider event type.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Provider reference in the webhook payload.")
    transaction_reference: str | None = Field(default=None, max_length=255, description="Platform transaction reference.")
    status: str | None = Field(default=None, max_length=50, description="Provider-reported payment status.")
    metadata: dict[str, object] | None = Field(default=None, description="Webhook metadata payload without secrets.")


class VirtualAccountCreateSchema(BaseModel):
    """Schema for creating a virtual account request."""

    model_config = ConfigDict(from_attributes=True)

    customer_reference: str = Field(..., max_length=255, description="Customer reference for the virtual account.")
    account_type: str | None = Field(default=None, max_length=100, description="Virtual account type.")
    provider_reference: str | None = Field(default=None, max_length=255, description="Optional provider reference.")
