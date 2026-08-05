from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class KYCBase(BaseModel):
    """Shared KYC fields for validation and serialization."""

    model_config = ConfigDict(from_attributes=True)

    verification_type: str | None = Field(default=None, max_length=100, description="KYC verification type.")
    verification_status: str | None = Field(default=None, max_length=50, description="Current KYC verification status.")
    verification_level: str | None = Field(default=None, max_length=50, description="Verification level.")
    document_type: str | None = Field(default=None, max_length=100, description="Submitted document type.")
    document_reference: str | None = Field(default=None, max_length=500, description="Reference to the submitted document.")
    country: str | None = Field(default=None, max_length=100, description="Country of the verification record.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class KYCCreate(KYCBase):
    """Schema for submitting a new KYC record."""

    verification_type: str = Field(..., max_length=100, description="KYC verification type.")
    document_type: str | None = Field(default=None, max_length=100, description="Submitted document type.")
    document_reference: str | None = Field(default=None, max_length=500, description="Reference to the submitted document.")
    verification_level: str | None = Field(default="basic", max_length=50, description="Verification level.")
    country: str | None = Field(default=None, max_length=100, description="Country of the verification record.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class KYCUpdate(BaseModel):
    """Schema for updating KYC status and metadata."""

    model_config = ConfigDict(from_attributes=True)

    verification_status: str | None = Field(default=None, max_length=50, description="Current KYC verification status.")
    verification_level: str | None = Field(default=None, max_length=50, description="Verification level.")
    document_verification_status: str | None = Field(default=None, max_length=50, description="Document verification status.")
    reviewed_by: str | None = Field(default=None, max_length=100, description="Reviewer reference.")
    compliance_notes: str | None = Field(default=None, description="Compliance notes.")
    metadata_payload: str | None = Field(default=None, description="Optional non-sensitive metadata payload.")


class KYCResponse(KYCBase):
    """Safe response schema for KYC records."""

    id: UUID = Field(..., description="KYC record identifier.")
    user_id: UUID = Field(..., description="Owning user identifier.")
    verification_status: str = Field(..., max_length=50, description="Current KYC verification status.")
    created_at: datetime = Field(..., description="KYC creation timestamp.")
    updated_at: datetime = Field(..., description="KYC update timestamp.")
    submitted_at: datetime | None = Field(default=None, description="Submission timestamp.")
    reviewed_at: datetime | None = Field(default=None, description="Review timestamp.")


class KYCApprovalSchema(BaseModel):
    """Schema for admin approval or review requests."""

    approval_status: str = Field(..., max_length=50, description="Approval or review outcome.")
    review_notes: str | None = Field(default=None, description="Review notes.")
