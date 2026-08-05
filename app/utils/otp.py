from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

from app.config.settings import settings


def generate_otp(length: int | None = None) -> str:
    """Generate a cryptographically secure numeric OTP."""
    otp_length = length or settings.otp_length
    return "".join(secrets.choice(string.digits) for _ in range(otp_length))


def get_otp_expiry(minutes: int | None = None) -> datetime:
    """Return the OTP expiration timestamp in UTC."""
    ttl_minutes = minutes or settings.otp_expiry_minutes
    return datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)


def is_otp_expired(expires_at: datetime) -> bool:
    """Return True when the provided expiration timestamp is in the past."""
    return datetime.now(timezone.utc) >= expires_at
