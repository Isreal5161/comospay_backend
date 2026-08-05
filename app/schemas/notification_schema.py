from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationBase(BaseModel):
    """Shared notification fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    title: str | None = Field(default=None, max_length=255, description="Notification title.")
    message: str | None = Field(default=None, description="Notification message body.")
    notification_type: str | None = Field(default=None, max_length=100, description="Notification type.")
    category: str | None = Field(default=None, max_length=100, description="Notification category.")
    channel: str | None = Field(default=None, max_length=50, description="Delivery channel.")


class NotificationCreate(NotificationBase):
    """Schema for creating a new notification."""

    user_id: UUID = Field(..., description="Receiving user identifier.")
    title: str | None = Field(default=None, max_length=255, description="Notification title.")
    message: str = Field(..., description="Notification message body.")
    channel: str = Field(..., max_length=50, description="Delivery channel.")
    notification_type: str | None = Field(default="info", max_length=100, description="Notification type.")
    category: str | None = Field(default="general", max_length=100, description="Notification category.")
    reference: str | None = Field(default=None, max_length=255, description="Optional related reference.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata.")


class NotificationResponse(NotificationBase):
    """Safe response schema for notification records."""

    id: UUID = Field(..., description="Notification identifier.")
    status: str = Field(..., max_length=50, description="Notification delivery status.")
    is_read: bool = Field(..., description="Whether the notification has been read.")
    is_archived: bool = Field(..., description="Whether the notification is archived.")
    created_at: datetime = Field(..., description="Notification creation timestamp.")
    updated_at: datetime = Field(..., description="Notification update timestamp.")
    sent_at: datetime | None = Field(default=None, description="Notification sent timestamp.")
    delivered_at: datetime | None = Field(default=None, description="Notification delivered timestamp.")
    read_at: datetime | None = Field(default=None, description="Notification read timestamp.")


class NotificationReadSchema(BaseModel):
    """Schema for marking notifications as read."""

    notification_id: UUID = Field(..., description="Notification identifier.")
