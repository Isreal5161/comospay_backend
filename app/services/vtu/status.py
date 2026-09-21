from __future__ import annotations

from typing import Any


def normalize_vtu_status(status: Any) -> str:
    """Normalize provider status values for VTU purchase workflows."""
    if status is None:
        return "pending"

    normalized = str(status).strip().lower()
    if not normalized:
        return "pending"

    mapping = {
        "success": "succeeded",
        "successful": "succeeded",
        "succeeded": "succeeded",
        "complete": "completed",
        "completed": "completed",
        "settled": "settled",
        "failed": "failed",
        "failure": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "reversed": "reversed",
        "pending": "pending",
        "processing": "pending",
        "in-progress": "pending",
        "queued": "pending",
        "duplicate": "duplicate",
        "timeout": "timeout",
    }
    return mapping.get(normalized, normalized)
