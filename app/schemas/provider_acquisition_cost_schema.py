from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderAcquisitionCostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID
    service_type: str = Field(min_length=3, max_length=50)
    network_identifier: str | None = Field(default=None, max_length=120)
    product_identifier: str | None = Field(default=None, max_length=180)
    currency: str = Field(min_length=3, max_length=10)
    cost_method: str = Field(pattern="^(fixed_amount|percentage_of_selling_amount)$")
    cost_value: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    effective_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    effective_until: datetime | None = None
    evidence_note: str = Field(min_length=1, max_length=2000)

    @field_validator("effective_from")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Effective dates must include a timezone.")
        return value

    @field_validator("effective_until")
    @classmethod
    def require_end_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Effective dates must include a timezone.")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()


class ProviderAcquisitionCostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: UUID | None = None
    service_type: str | None = Field(default=None, min_length=3, max_length=50)
    network_identifier: str | None = Field(default=None, max_length=120)
    product_identifier: str | None = Field(default=None, max_length=180)
    currency: str | None = Field(default=None, min_length=3, max_length=10)
    cost_method: str | None = Field(default=None, pattern="^(fixed_amount|percentage_of_selling_amount)$")
    cost_value: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    evidence_note: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("effective_from")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Effective dates must include a timezone.")
        return value

    @field_validator("effective_until")
    @classmethod
    def require_end_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Effective dates must include a timezone.")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None
