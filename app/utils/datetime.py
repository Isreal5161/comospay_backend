from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def get_utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def get_local_now(timezone_name: str = "UTC") -> datetime:
    """Return the current time in the provided timezone."""
    from zoneinfo import ZoneInfo

    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now(timezone.utc)


def format_datetime(value: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a timezone-aware datetime into a string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.strftime(fmt)


def parse_datetime(value: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """Parse a datetime string into a timezone-aware UTC datetime."""
    parsed = datetime.strptime(value, fmt)
    return parsed.replace(tzinfo=timezone.utc)


def add_expiration(delta_seconds: int) -> datetime:
    """Return a timezone-aware datetime offset from now by the provided number of seconds."""
    return get_utc_now() + timedelta(seconds=delta_seconds)
