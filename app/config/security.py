from __future__ import annotations

import hmac
import secrets
import string
from typing import Final

from passlib.context import CryptContext

from app.config.settings import settings


_pwd_context: Final[CryptContext] = CryptContext(
    # Prefer Argon2 for new hashes but accept bcrypt for legacy values.
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
)


def validate_password_strength(password: str) -> bool:
    """Validate that a password meets the minimum configured strength requirements."""
    if len(password) < settings.password_min_length:
        return False

    has_upper = any(char.isupper() for char in password)
    has_lower = any(char.islower() for char in password)
    has_digit = any(char.isdigit() for char in password)
    has_special = any(char in string.punctuation for char in password)

    return all([has_upper, has_lower, has_digit, has_special])


def hash_password(password: str) -> str:
    """Hash a password using a strong adaptive hashing algorithm."""
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a hashed password."""
    return _pwd_context.verify(password, hashed_password)


def hash_pin(pin: str) -> str:
    """Hash a PIN using a strong one-way hashing algorithm."""
    return _pwd_context.hash(pin)


def verify_pin(pin: str, hashed_pin: str) -> bool:
    """Verify a plaintext PIN against a hashed PIN."""
    return _pwd_context.verify(pin, hashed_pin)


def validate_pin(pin: str, length: int | None = None) -> bool:
    """Validate that a PIN contains only digits and meets the configured length."""
    expected_length = length or settings.pin_length
    if len(pin) != expected_length:
        return False
    return pin.isdigit()


def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key(length: int = 32) -> str:
    """Generate a cryptographically secure API key."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_webhook_secret(length: int = 32) -> str:
    """Generate a cryptographically secure webhook secret."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_secure_string(length: int = 24, *, alphabet: str | None = None) -> str:
    """Generate a cryptographically secure random string."""
    chars = alphabet or (string.ascii_letters + string.digits)
    return "".join(secrets.choice(chars) for _ in range(length))


def generate_secure_code(length: int = 6) -> str:
    """Generate a cryptographically secure numeric code."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_otp(length: int | None = None) -> str:
    """Generate a numeric OTP using secure randomness."""
    otp_length = length or settings.otp_length
    return generate_secure_code(otp_length)


def constant_time_compare(left: str, right: str) -> bool:
    """Compare two strings using constant-time semantics."""
    return hmac.compare_digest(left, right)


def validate_email(email: str) -> bool:
    """Validate the structure of an email address."""
    if not isinstance(email, str):
        return False
    if "@" not in email or email.count("@") != 1:
        return False
    local_part, domain = email.split("@", 1)
    if not local_part or not domain or "." not in domain:
        return False
    return True


def normalize_phone_number(phone: str) -> str:
    """Normalize a phone number to a consistent E.164-like format."""
    if not phone:
        return ""
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch in "+")
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("0"):
        return f"234{cleaned[1:]}"
    return cleaned
