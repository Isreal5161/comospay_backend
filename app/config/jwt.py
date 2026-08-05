from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import ExpiredSignatureError, JWTError, jwt

from app.config.settings import settings


def _get_secret_key() -> str:
    """Return the configured JWT signing secret as a string."""
    if settings.jwt_secret_key is None:
        raise RuntimeError("JWT secret key is not configured")
    return settings.jwt_secret_key.get_secret_value()


def _build_claims(subject: str, token_type: str, ttl: timedelta, extra_claims: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a standard JWT payload with timezone-aware timestamps."""
    issued_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "type": token_type,
    }
    if extra_claims:
        payload.update(extra_claims)
    return payload


# Token utility: creates signed access tokens for API authentication.
def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Create a signed access token with a configured expiration window."""
    payload = _build_claims(
        subject=subject,
        token_type="access",
        ttl=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims=extra_claims,
    )
    return jwt.encode(payload, _get_secret_key(), algorithm=settings.jwt_algorithm)


# Token utility: creates signed refresh tokens for session renewal.
def create_refresh_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Create a signed refresh token with a configured expiration window."""
    payload = _build_claims(
        subject=subject,
        token_type="refresh",
        ttl=timedelta(days=settings.refresh_token_expire_days),
        extra_claims=extra_claims,
    )
    return jwt.encode(payload, _get_secret_key(), algorithm=settings.jwt_algorithm)


# Token utility: decodes and validates a JWT payload.
def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT, returning its claims or raising a value error."""
    try:
        return jwt.decode(
            token,
            _get_secret_key(),
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": True},
        )
    except ExpiredSignatureError as exc:
        raise ValueError("Token has expired") from exc
    except JWTError as exc:
        raise ValueError("Invalid token") from exc


# Token utility: validates a JWT and returns its claims.
def verify_token(token: str) -> dict[str, Any]:
    """Verify a JWT and return its claims."""
    return decode_token(token)
