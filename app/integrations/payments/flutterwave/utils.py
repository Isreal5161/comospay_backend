from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


def parse_json_payload(value: Any) -> Any:
    """Safely parse a JSON string or return the original value when parsing is unnecessary."""
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def normalize_currency(value: str | None) -> str | None:
    """Normalize a currency code to uppercase and return None for blank input."""
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def validate_currency(value: str | None) -> bool:
    """Return True when a currency code is non-empty after normalization."""
    return bool(normalize_currency(value))


def normalize_phone_number(value: str | None) -> str | None:
    """Normalize a phone number to E.164 form when possible."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("0"):
        return f"+234{cleaned[1:]}"
    return cleaned


def normalize_email(value: str | None) -> str | None:
    """Normalize an email address by trimming whitespace."""
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def sanitize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy of a mapping with empty values removed."""
    if not payload:
        return {}
    return {key: value for key, value in payload.items() if value not in (None, "", [], {}, ())}


def remove_none_values(value: Any) -> Any:
    """Recursively remove None values from nested dictionaries and lists."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned[key] = remove_none_values(item)
        return cleaned
    if isinstance(value, list):
        cleaned_items = [remove_none_values(item) for item in value if item is not None]
        return cleaned_items
    return value


def parse_flutterwave_timestamp(value: str | None) -> datetime | None:
    """Convert a Flutterwave timestamp into a timezone-aware datetime object."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_idempotency_key(*values: Any) -> str:
    """Create a stable idempotency key from one or more values."""
    parts = [str(value) for value in values if value is not None]
    base = "-".join(parts) if parts else str(uuid4())
    return base if len(base) <= 64 else base[:64]


def generate_request_reference(prefix: str = "txn") -> str:
    """Generate a unique request reference for provider calls."""
    return f"{prefix}-{uuid4().hex}"


def normalize_provider_status(value: str | None) -> str:
    """Map provider status values to a normalized internal status string."""
    if value is None:
        return "unknown"
    normalized = value.strip().lower()
    mapping = {
        "success": "success",
        "successful": "success",
        "completed": "success",
        "pending": "pending",
        "processing": "pending",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    return mapping.get(normalized, normalized or "unknown")


def get_nested_value(data: Mapping[str, Any] | None, *path: str) -> Any:
    """Safely extract a nested dictionary value using a path of keys."""
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def format_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Format metadata dictionaries for provider requests while removing empty values."""
    if not metadata:
        return {}
    return sanitize_payload({str(key): value for key, value in metadata.items()})
