from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SupportTicketMessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sender_id: UUID | None = None
    sender_name: str = Field(..., min_length=1, max_length=255)
    sender_type: str = Field(..., pattern=r"^(customer|admin)$")
    content: str = Field(..., min_length=1, max_length=5000)
    created_at: datetime
    is_internal: bool = False


class SupportTicketSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    user_name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    initial_message: str = Field(..., min_length=1)
    status: str = Field(..., pattern=r"^(open|in_progress|waiting_customer|resolved|closed)$")
    priority: str = Field(..., pattern=r"^(low|normal|high|urgent)$")
    category: str = Field(..., min_length=1, max_length=100)
    assignee_id: UUID | None = None
    assignee_name: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[SupportTicketMessageSchema] = Field(default_factory=list)


class SupportTicketListData(BaseModel):
    items: list[SupportTicketSchema]
    page: int
    page_size: int
    total: int
    total_pages: int


class SupportTicketReplyRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    is_internal: bool = False


class SupportTicketStatusUpdate(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(open|in_progress|waiting_customer|resolved|closed)$")
    priority: str | None = Field(default=None, pattern=r"^(low|normal|high|urgent)$")
    assignee_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)
