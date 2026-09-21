from __future__ import annotations

from typing import Any


_STATUS_MAP: dict[str, str] = {
    "success": "succeeded",
    "successful": "succeeded",
    "completed": "succeeded",
    "complete": "succeeded",
    "settled": "succeeded",
    "paid": "succeeded",
    "processing": "pending",
    "pending": "pending",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "declined": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def normalize_payment_status(value: Any, *, default: str = "pending") -> str:
    """Normalize provider and internal payment statuses to canonical lifecycle values."""
    if value is None:
        normalized_default = str(default).strip().lower() or "pending"
        return _STATUS_MAP.get(normalized_default, normalized_default)

    normalized = str(value).strip().lower()
    if not normalized:
        normalized_default = str(default).strip().lower() or "pending"
        return _STATUS_MAP.get(normalized_default, normalized_default)

    return _STATUS_MAP.get(normalized, normalized)
