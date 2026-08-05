from __future__ import annotations

import string
from typing import Final

from passlib.context import CryptContext

from app.config.settings import settings


class PasswordValidationError(ValueError):
    """Raised when a password does not satisfy the configured policy."""


# Strong password hashing context using Argon2, which is suitable for enterprise-grade security.
_pwd_context: Final[CryptContext] = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
)


def hash_password(password: str) -> str:
    """Hash a plaintext password using a strong adaptive hashing algorithm."""
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a hashed password."""
    return _pwd_context.verify(password, hashed_password)


def validate_password_strength(password: str) -> None:
    """Validate a password against the configured strength policy."""
    if len(password) < settings.password_min_length:
        raise PasswordValidationError(
            f"Password must be at least {settings.password_min_length} characters long."
        )

    if not any(char.isupper() for char in password):
        raise PasswordValidationError("Password must contain at least one uppercase letter.")

    if not any(char.islower() for char in password):
        raise PasswordValidationError("Password must contain at least one lowercase letter.")

    if not any(char.isdigit() for char in password):
        raise PasswordValidationError("Password must contain at least one number.")

    if not any(char in string.punctuation for char in password):
        raise PasswordValidationError("Password must contain at least one special character.")
