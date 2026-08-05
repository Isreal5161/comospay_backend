from __future__ import annotations

import secrets
import string
from typing import Final


# URL-safe alphabet for generating non-JWT random tokens.
_URL_SAFE_ALPHABET: Final[str] = string.ascii_letters + string.digits + "-_."


def generate_secure_token(length: int = 32, url_safe: bool = True) -> str:
    """Generate a cryptographically secure random token."""
    alphabet = _URL_SAFE_ALPHABET if url_safe else string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key(length: int = 32) -> str:
    """Generate a secure API key suitable for service-to-service authentication."""
    return generate_secure_token(length=length, url_safe=False)


def generate_verification_token(length: int = 16) -> str:
    """Generate a verification token for email or phone confirmation flows."""
    return generate_secure_token(length=length, url_safe=False)
