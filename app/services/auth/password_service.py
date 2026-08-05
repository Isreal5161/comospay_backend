from __future__ import annotations

import json
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.config.redis import get_redis
from app.config.security import hash_password as _hash_password
from app.config.security import verify_password as _verify_password
from app.config.security import validate_password_strength as _validate_password_strength
from app.config.settings import settings
from app.utils.exceptions import AuthenticationException, ValidationException
from app.utils.logger import get_logger, log_security_event


class WeakPasswordException(ValidationException):
    """Raised when a supplied password fails the configured strength policy."""

    def __init__(self, detail: str = "Password does not meet the required strength.", error_code: str = "WEAK_PASSWORD") -> None:
        super().__init__(detail=detail, error_code=error_code)


class InvalidPasswordException(AuthenticationException):
    """Raised when a supplied password is missing or invalid for the requested operation."""

    def __init__(self, detail: str = "Password is invalid.", error_code: str = "INVALID_PASSWORD") -> None:
        super().__init__(detail=detail, error_code=error_code)


class ExpiredResetTokenException(AuthenticationException):
    """Raised when a password-reset token has expired."""

    def __init__(self, detail: str = "Password reset token has expired.", error_code: str = "RESET_TOKEN_EXPIRED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class InvalidResetTokenException(AuthenticationException):
    """Raised when a password-reset token is malformed or unusable."""

    def __init__(self, detail: str = "Password reset token is invalid.", error_code: str = "INVALID_RESET_TOKEN") -> None:
        super().__init__(detail=detail, error_code=error_code)


class PasswordReuseViolationException(ValidationException):
    """Raised when a password matches a previously used password."""

    def __init__(self, detail: str = "Password reuse is not allowed.", error_code: str = "PASSWORD_REUSE_VIOLATION") -> None:
        super().__init__(detail=detail, error_code=error_code)


class PasswordPolicyViolationException(ValidationException):
    """Raised when a password violates the configured policy."""

    def __init__(self, detail: str = "Password policy violation.", error_code: str = "PASSWORD_POLICY_VIOLATION") -> None:
        super().__init__(detail=detail, error_code=error_code)


class PasswordService:
    """Service layer implementation for password lifecycle and password-reset handling."""

    _RESET_TOKEN_PREFIX = "password_reset"
    _PASSWORD_HISTORY_PREFIX = "password_history"
    _COMMON_PASSWORDS = {
        "password",
        "password123",
        "password123!",
        "qwerty",
        "qwerty123",
        "letmein",
        "admin",
        "welcome",
        "secret",
        "iloveyou",
        "123456",
        "12345678",
        "123456789",
        "passw0rd",
    }

    def __init__(self, *, redis_client: Any | None = None, logger: logging.Logger | None = None) -> None:
        """Initialize the password service with injectable dependencies."""
        self.redis_client = redis_client
        self.logger = logger or get_logger(__name__)

    def hash_password(self, password: str) -> str:
        """Hash a plaintext password using the configured adaptive hashing algorithm."""
        if not password:
            raise InvalidPasswordException(detail="Password is required.")
        return _hash_password(password)

    def verify_password(self, password: str, hashed_password: str) -> bool:
        """Verify a plaintext password against a stored hash using constant-time semantics."""
        if not password or not hashed_password:
            return False
        return _verify_password(password, hashed_password)

    def validate_password_strength(self, password: str) -> bool:
        """Validate the password against the configured strength rules."""
        if not isinstance(password, str) or not password:
            raise InvalidPasswordException(detail="Password is required.")

        if not _validate_password_strength(password):
            raise WeakPasswordException()

        policy = self._password_policy()
        if len(password) > policy["max_length"]:
            raise WeakPasswordException(detail="Password exceeds the maximum allowed length.")
        if policy["max_repeated_characters"] is not None and self._has_excessive_repetition(password, policy["max_repeated_characters"]):
            raise WeakPasswordException(detail="Password contains too many repeated characters.")
        return True

    def check_password_complexity(self, password: str) -> bool:
        """Return True when the password satisfies the configured complexity requirements."""
        policy = self._password_policy()
        if not password:
            return False
        if len(password) < policy["min_length"]:
            return False
        if len(password) > policy["max_length"]:
            return False
        if policy["require_uppercase"] and not any(char.isupper() for char in password):
            return False
        if policy["require_lowercase"] and not any(char.islower() for char in password):
            return False
        if policy["require_numeric"] and not any(char.isdigit() for char in password):
            return False
        if policy["require_special"] and not any(char in string.punctuation for char in password):
            return False
        if policy["max_repeated_characters"] is not None and self._has_excessive_repetition(password, policy["max_repeated_characters"]):
            return False
        return True

    def compare_password_confirmation(self, password: str, confirmation: str) -> bool:
        """Compare a password and its confirmation using constant-time semantics."""
        if not password or not confirmation:
            raise InvalidPasswordException(detail="Password confirmation is required.")
        if not secrets.compare_digest(password, confirmation):
            raise InvalidPasswordException(detail="Password confirmation does not match.")
        return True

    async def detect_password_reuse(
        self,
        *,
        user_id: str,
        password: str,
        historical_passwords: Sequence[str] | None = None,
    ) -> bool:
        """Return True when a password matches any previously stored password hash."""
        if not user_id:
            raise InvalidPasswordException(detail="User identifier is required.")
        if not password:
            raise InvalidPasswordException(detail="Password is required.")

        histories = list(historical_passwords or await self._load_password_history(user_id))
        for previous_hash in histories:
            if previous_hash and self.verify_password(password, previous_hash):
                log_security_event(self.logger, "Password reuse detected", user_id=user_id)
                return True
        return False

    def generate_secure_temporary_password(self) -> str:
        """Generate a strong temporary password that satisfies the configured policy."""
        policy = self._password_policy()
        length = max(policy["min_length"], 16)
        alphabet = string.ascii_letters + string.digits + string.punctuation
        while True:
            candidate = "".join(secrets.choice(alphabet) for _ in range(length))
            if self.check_password_complexity(candidate):
                return candidate

    async def generate_password_reset_token(self, *, user_id: str, ttl_minutes: int | None = None) -> str:
        """Create a password reset token and persist it to Redis for the configured lifetime."""
        if not user_id:
            raise InvalidPasswordException(detail="User identifier is required.")

        token = secrets.token_urlsafe(32)
        payload = {
            "user_id": str(user_id),
            "created_at": int(datetime.now(timezone.utc).timestamp()),
        }
        ttl_seconds = (ttl_minutes or self._password_reset_ttl_minutes()) * 60
        redis_client = await self._get_redis_client()
        if redis_client is not None:
            await redis_client.set(self._reset_token_key(token), json.dumps(payload), ex=ttl_seconds)
        return token

    async def verify_password_reset_token(self, token: str) -> dict[str, Any]:
        """Verify the validity of a password reset token and return its payload."""
        if not token or not self._looks_like_reset_token(token):
            raise InvalidResetTokenException()

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise ExpiredResetTokenException(detail="Password reset token service is unavailable.")

        payload_raw = await redis_client.get(self._reset_token_key(token))
        if not payload_raw:
            raise ExpiredResetTokenException()

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            raise InvalidResetTokenException() from exc

        return payload

    async def invalidate_password_reset_token(self, token: str) -> bool:
        """Invalidate a password reset token and remove it from Redis if present."""
        if not token or not self._looks_like_reset_token(token):
            raise InvalidResetTokenException()

        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False

        deleted = await redis_client.delete(self._reset_token_key(token))
        return bool(deleted)

    def calculate_password_entropy(self, password: str) -> float:
        """Estimate the Shannon entropy of a password based on the character set size."""
        if not password:
            return 0.0
        character_set = len(set(password))
        return round(len(password) * (character_set and (math.log2(character_set) if character_set > 1 else 0.0) or 0.0), 2)

    def detect_weak_common_passwords(self, password: str) -> bool:
        """Return True when the password appears in a conservative list of common passwords."""
        if not password:
            return False
        normalized = password.strip().lower()
        return normalized in self._COMMON_PASSWORDS

    async def enforce_password_policy(
        self,
        *,
        password: str,
        confirmation: str | None = None,
        user_id: str | None = None,
        historical_passwords: Sequence[str] | None = None,
    ) -> bool:
        """Evaluate the password against all configured policy rules and raise clear exceptions on failure."""
        if not password:
            raise InvalidPasswordException(detail="Password is required.")
        if confirmation is not None:
            self.compare_password_confirmation(password, confirmation)

        self.validate_password_strength(password)
        if not self.check_password_complexity(password):
            raise PasswordPolicyViolationException(detail="Password does not satisfy the configured complexity policy.")
        if self.detect_weak_common_passwords(password):
            raise WeakPasswordException(detail="Password is too common and is not allowed.")

        if user_id is not None:
            if await self.detect_password_reuse(user_id=user_id, password=password, historical_passwords=historical_passwords):
                raise PasswordReuseViolationException()

        return True

    def determine_password_expiration(self, *, password_changed_at: datetime | None = None) -> datetime | None:
        """Return the expiration time for a password when expiration is enabled."""
        if not self._password_expiration_enabled():
            return None
        base_time = password_changed_at or datetime.now(timezone.utc)
        days = self._password_expiration_days()
        return base_time + timedelta(days=days)

    async def store_password_history(self, *, user_id: str, password_hash: str) -> None:
        """Persist a password hash to Redis history for reuse detection."""
        if not user_id or not password_hash:
            raise InvalidPasswordException(detail="User identifier and password hash are required.")

        redis_client = await self._get_redis_client()
        if redis_client is None:
            return

        key = self._password_history_key(user_id)
        existing = await redis_client.lrange(key, 0, -1)
        if password_hash not in existing:
            await redis_client.lpush(key, password_hash)
            limit = self._password_history_limit()
            if limit > 0:
                await redis_client.ltrim(key, 0, limit - 1)
            await redis_client.expire(key, 60 * 60 * 24 * 365)

    async def _load_password_history(self, user_id: str) -> list[str]:
        """Load historical password hashes from Redis for a user."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return []
        return [item for item in await redis_client.lrange(self._password_history_key(user_id), 0, -1) if item]

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client when available, otherwise None."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("Redis unavailable for password operations: %s", exc)
            return None

    def _password_policy(self) -> dict[str, Any]:
        """Read password policy values from settings or environment variables."""
        return {
            "min_length": self._read_int_value("PASSWORD_MIN_LENGTH", default=getattr(settings, "password_min_length", 12)),
            "max_length": self._read_int_value("PASSWORD_MAX_LENGTH", default=getattr(settings, "password_max_length", 128)),
            "require_uppercase": self._read_bool_value("PASSWORD_REQUIRE_UPPERCASE", default=True),
            "require_lowercase": self._read_bool_value("PASSWORD_REQUIRE_LOWERCASE", default=True),
            "require_numeric": self._read_bool_value("PASSWORD_REQUIRE_NUMERIC", default=True),
            "require_special": self._read_bool_value("PASSWORD_REQUIRE_SPECIAL", default=True),
            "max_repeated_characters": self._read_int_value("PASSWORD_MAX_REPEATED_CHARACTERS", default=None),
            "password_history_limit": self._read_int_value("PASSWORD_HISTORY_LIMIT", default=5),
        }

    def _password_reset_ttl_minutes(self) -> int:
        """Return the configured password-reset token lifetime in minutes."""
        return self._read_int_value("PASSWORD_RESET_TOKEN_TTL_MINUTES", default=15)

    def _password_expiration_enabled(self) -> bool:
        """Return whether password expiration is enabled."""
        return self._read_bool_value("PASSWORD_EXPIRATION_ENABLED", default=False)

    def _password_expiration_days(self) -> int:
        """Return the configured password expiration period in days."""
        return self._read_int_value("PASSWORD_EXPIRATION_DAYS", default=90)

    def _password_history_limit(self) -> int:
        """Return the configured password-history retention limit."""
        return self._read_int_value("PASSWORD_HISTORY_LIMIT", default=5)

    def _read_int_value(self, name: str, *, default: int | None) -> int | None:
        """Read an integer value from settings or environment variables."""
        if hasattr(settings, name.lower()):
            value = getattr(settings, name.lower(), None)
            if isinstance(value, int):
                return value
        env_value = os.getenv(name)
        if env_value is None:
            return default
        try:
            return int(env_value)
        except ValueError:
            return default

    def _read_bool_value(self, name: str, *, default: bool) -> bool:
        """Read a boolean value from settings or environment variables."""
        if hasattr(settings, name.lower()):
            value = getattr(settings, name.lower(), None)
            if isinstance(value, bool):
                return value
        env_value = os.getenv(name)
        if env_value is None:
            return default
        return env_value.strip().lower() in {"1", "true", "yes", "on"}

    def _has_excessive_repetition(self, password: str, max_repeated_characters: int) -> bool:
        """Return True when a password repeats a character too often."""
        if max_repeated_characters <= 0:
            return False
        counts: dict[str, int] = {}
        for char in password:
            counts[char] = counts.get(char, 0) + 1
            if counts[char] > max_repeated_characters:
                return True
        return False

    def _reset_token_key(self, token: str) -> str:
        """Build a Redis key for password reset token storage."""
        return f"{self._RESET_TOKEN_PREFIX}:{token}"

    def _password_history_key(self, user_id: str) -> str:
        """Build a Redis key for password history storage."""
        return f"{self._PASSWORD_HISTORY_PREFIX}:{user_id}"

    def _looks_like_reset_token(self, token: str) -> bool:
        """Return True when the supplied reset token resembles a random token."""
        return bool(token) and len(token) >= 16 and all(ch.isalnum() or ch in "-_.~" for ch in token)


import math


__all__ = ["PasswordService", "WeakPasswordException", "InvalidPasswordException", "ExpiredResetTokenException", "InvalidResetTokenException", "PasswordReuseViolationException", "PasswordPolicyViolationException"]
