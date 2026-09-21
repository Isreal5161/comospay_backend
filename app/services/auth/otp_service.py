from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.config.redis import get_redis
from app.config.settings import settings
from app.utils.exceptions import AuthenticationException, ValidationException
from app.utils.logger import get_logger, log_security_event


class InvalidOtpException(AuthenticationException):
    """Raised when an OTP code is invalid or does not match the stored reference."""

    def __init__(self, detail: str = "The OTP provided is invalid.", error_code: str = "INVALID_OTP") -> None:
        super().__init__(detail=detail, error_code=error_code)


class ExpiredOtpException(AuthenticationException):
    """Raised when an OTP has expired and can no longer be used."""

    def __init__(self, detail: str = "The OTP has expired.", error_code: str = "OTP_EXPIRED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class OtpAlreadyUsedException(ValidationException):
    """Raised when an OTP has already been used or invalidated."""

    def __init__(self, detail: str = "The OTP has already been used.", error_code: str = "OTP_ALREADY_USED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class TooManyVerificationAttemptsException(ValidationException):
    """Raised when verification attempts exceed the configured limit."""

    def __init__(self, detail: str = "Too many OTP verification attempts.", error_code: str = "OTP_VERIFICATION_LIMIT_EXCEEDED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class TooManyResendAttemptsException(ValidationException):
    """Raised when resend attempts exceed the configured limit."""

    def __init__(self, detail: str = "Too many OTP resend attempts.", error_code: str = "OTP_RESEND_LIMIT_EXCEEDED") -> None:
        super().__init__(detail=detail, error_code=error_code)


class ResendCooldownActiveException(ValidationException):
    """Raised when an OTP resend is attempted before the cooldown period has elapsed."""

    def __init__(self, detail: str = "OTP resend is still in cooldown.", error_code: str = "OTP_RESEND_COOLDOWN_ACTIVE") -> None:
        super().__init__(detail=detail, error_code=error_code)


class UnsupportedOtpTypeException(ValidationException):
    """Raised when an unsupported OTP purpose is requested."""

    def __init__(self, detail: str = "Unsupported OTP type.", error_code: str = "UNSUPPORTED_OTP_TYPE") -> None:
        super().__init__(detail=detail, error_code=error_code)


class OTPService:
    """Service-layer implementation for issuing, validating, and rotating OTPs."""

    _SUPPORTED_PURPOSES = {
        "email_verification",
        "login_verification",
        "password_reset",
        "change_email",
        "change_phone",
        "high_risk_transaction_verification",
        "wallet_verification",
        "device_verification",
    }
    _OTP_PREFIX = "otp"
    _ATTEMPT_PREFIX = "otp_attempt"
    _RESEND_PREFIX = "otp_resend"
    _RATE_LIMIT_PREFIX = "otp_rate_limit"

    def __init__(self, *, redis_client: Any | None = None, notification_service: Any | None = None, logger: logging.Logger | None = None) -> None:
        """Initialize the OTP service with injectable dependencies."""
        self.redis_client = redis_client
        self.notification_service = notification_service
        self.logger = logger or get_logger(__name__)

    def generate_otp(self, *, length: int | None = None) -> str:
        """Generate a cryptographically secure OTP value."""
        otp_length = length or self._otp_length()
        return "".join(secrets.choice(string.digits) for _ in range(otp_length))

    async def send_otp(
        self,
        *,
        recipient: str,
        purpose: str,
        channel: str = "email",
        user_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        ttl_minutes: int | None = None,
        delivery_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate, store, and dispatch an OTP for the requested purpose and channel."""
        self._validate_purpose(purpose)
        self._validate_channel(channel)
        if not recipient:
            raise ValidationException("A recipient is required to send an OTP.")

        await self._assert_not_rate_limited(identifier=user_id or recipient, purpose=purpose)
        await self._assert_resend_allowed(identifier=user_id or recipient, purpose=purpose)

        otp_code = self.generate_otp()
        reference_id = self.generate_secure_otp_reference_id()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes or self._otp_expiry_minutes())
        payload = {
            "purpose": purpose,
            "recipient": recipient,
            "user_id": user_id,
            "otp_hash": self._hash_secret(otp_code),
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 0,
            "used": False,
            "metadata": dict(metadata or {}),
        }

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for OTP storage.")

        await redis_client.set(self._otp_key(reference_id), json.dumps(payload), ex=self._ttl_seconds(expires_at))
        await self._record_resend(identifier=user_id or recipient, purpose=purpose)
        await self._record_rate_limit(identifier=user_id or recipient, purpose=purpose)

        delivery_result = await self._dispatch_otp(
            recipient=recipient,
            otp_code=otp_code,
            purpose=purpose,
            channel=channel,
            expires_at=expires_at,
            delivery_context=delivery_context,
        )

        log_security_event(
            self.logger,
            "OTP issued",
            reference_id=reference_id,
            purpose=purpose,
            channel=channel,
            user_id=user_id,
        )
        return {
            "reference_id": reference_id,
            "expires_at": expires_at.isoformat(),
            "purpose": purpose,
            "channel": channel,
            "delivery": delivery_result,
        }

    async def verify_otp(self, *, reference_id: str, otp_code: str, purpose: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """Verify an OTP and invalidate it immediately after successful use."""
        if not reference_id:
            raise InvalidOtpException(detail="A reference identifier is required.")
        if not otp_code:
            raise InvalidOtpException(detail="An OTP value is required.")

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for OTP verification.")

        payload_raw = await redis_client.get(self._otp_key(reference_id))
        if not payload_raw:
            raise ExpiredOtpException()

        payload = json.loads(payload_raw)
        if payload.get("used"):
            raise OtpAlreadyUsedException()

        if purpose and payload.get("purpose") != purpose:
            raise UnsupportedOtpTypeException(detail="OTP purpose does not match the requested context.")

        if await self.check_otp_expiration(reference_id=reference_id):
            await redis_client.delete(self._otp_key(reference_id))
            raise ExpiredOtpException()

        attempts = int(payload.get("attempts", 0)) + 1
        if attempts > self._max_verification_attempts():
            await redis_client.delete(self._otp_key(reference_id))
            raise TooManyVerificationAttemptsException()

        if self._hash_secret(otp_code) != payload.get("otp_hash"):
            payload["attempts"] = attempts
            await redis_client.set(self._otp_key(reference_id), json.dumps(payload), ex=self._ttl_seconds_from_payload(payload))
            raise InvalidOtpException()

        payload["used"] = True
        payload["attempts"] = attempts
        await redis_client.set(self._otp_key(reference_id), json.dumps(payload), ex=60)
        await redis_client.delete(self._otp_key(reference_id))
        log_security_event(self.logger, "OTP verified", reference_id=reference_id, user_id=user_id, purpose=payload.get("purpose"))
        return {"verified": True, "reference_id": reference_id, "purpose": payload.get("purpose")}

    async def resend_otp(self, *, reference_id: str, purpose: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """Invalidate the previous OTP and issue a new one after applying resend safeguards."""
        if not reference_id:
            raise InvalidOtpException(detail="A reference identifier is required.")

        self._validate_purpose(purpose or "")
        await self._assert_resend_allowed(identifier=user_id or reference_id, purpose=purpose or "email_verification")

        redis_client = await self._get_redis_client()
        if redis_client is None:
            raise RuntimeError("Redis is required for OTP resend.")

        existing = await redis_client.get(self._otp_key(reference_id))
        if existing:
            await redis_client.delete(self._otp_key(reference_id))

        return await self.send_otp(
            recipient=self._extract_recipient(existing),
            purpose=purpose or "email_verification",
            user_id=user_id,
        )

    async def invalidate_otp(self, *, reference_id: str) -> bool:
        """Invalidate an OTP reference immediately."""
        if not reference_id:
            return False
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False
        await redis_client.delete(self._otp_key(reference_id))
        return True

    async def check_otp_expiration(self, *, reference_id: str) -> bool:
        """Return True when an OTP has expired, False otherwise."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return True
        payload_raw = await redis_client.get(self._otp_key(reference_id))
        if not payload_raw:
            return True
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            return True
        expires_at = payload.get("expires_at")
        if not expires_at:
            return True
        return datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at)

    async def check_max_verification_attempts(self, *, reference_id: str) -> bool:
        """Return True when the verification attempt limit has been reached."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False
        payload_raw = await redis_client.get(self._otp_key(reference_id))
        if not payload_raw:
            return True
        payload = json.loads(payload_raw)
        return int(payload.get("attempts", 0)) >= self._max_verification_attempts()

    async def check_resend_cooldown(self, *, identifier: str, purpose: str) -> bool:
        """Return True when the resend cooldown is still active."""
        self._validate_purpose(purpose)
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False
        timestamp_raw = await redis_client.get(self._resend_key(identifier, purpose))
        if not timestamp_raw:
            return False
        timestamp = datetime.fromtimestamp(float(timestamp_raw), tz=timezone.utc)
        return datetime.now(timezone.utc) < timestamp + timedelta(seconds=self._resend_cooldown_seconds())

    async def check_rate_limiting(self, *, identifier: str, purpose: str) -> bool:
        """Return True when the rate limit threshold has been reached."""
        self._validate_purpose(purpose)
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False
        count = await redis_client.get(self._rate_limit_key(identifier, purpose))
        return int(count or 0) >= self._max_rate_limit_attempts()

    def generate_secure_otp_reference_id(self) -> str:
        """Create a cryptographically secure reference identifier for an OTP."""
        return secrets.token_urlsafe(16)

    def generate_otp_metadata(self, *, purpose: str, recipient: str, user_id: str | None = None) -> dict[str, Any]:
        """Build metadata suitable for storage with an OTP payload."""
        self._validate_purpose(purpose)
        return {
            "purpose": purpose,
            "recipient": recipient,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    async def cleanup_expired_otps(self) -> int:
        """Remove expired OTP records from Redis and return the number removed."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return 0
        removed = 0
        async for key in redis_client.scan_iter(match=f"{self._OTP_PREFIX}:*"):
            payload_raw = await redis_client.get(key)
            if not payload_raw:
                continue
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue
            expires_at = payload.get("expires_at")
            if expires_at and datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at):
                await redis_client.delete(key)
                removed += 1
        return removed

    def _validate_purpose(self, purpose: str) -> None:
        """Validate that the supplied OTP purpose is supported by the service."""
        if not purpose or purpose not in self._SUPPORTED_PURPOSES:
            raise UnsupportedOtpTypeException()

    def _validate_channel(self, channel: str) -> None:
        """Validate that the delivery channel is supported."""
        if channel not in {"email", "sms"}:
            raise ValidationException("Unsupported delivery channel.")

    async def _assert_not_rate_limited(self, *, identifier: str, purpose: str) -> None:
        """Raise when the identifier has exceeded the configured rate limit."""
        if await self.check_rate_limiting(identifier=identifier, purpose=purpose):
            raise TooManyResendAttemptsException(detail="OTP rate limit exceeded.")

    async def _assert_resend_allowed(self, *, identifier: str, purpose: str) -> None:
        """Raise when a resend is attempted before the configured cooldown expires."""
        if await self.check_resend_cooldown(identifier=identifier, purpose=purpose):
            raise ResendCooldownActiveException()

    async def _record_resend(self, *, identifier: str, purpose: str) -> None:
        """Persist the resend timestamp for the identifier and purpose."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return
        await redis_client.set(self._resend_key(identifier, purpose), datetime.now(timezone.utc).timestamp(), ex=self._resend_cooldown_seconds())

    async def _record_rate_limit(self, *, identifier: str, purpose: str) -> None:
        """Increment the rate-limit counter for the identifier and purpose."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            return
        key = self._rate_limit_key(identifier, purpose)
        await redis_client.incr(key)
        await redis_client.expire(key, self._rate_limit_window_seconds())

    async def _dispatch_otp(
        self,
        *,
        recipient: str,
        otp_code: str,
        purpose: str,
        channel: str,
        expires_at: datetime,
        delivery_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch an OTP through the existing notification service abstraction."""
        if self.notification_service is None:
            return {"status": "queued", "channel": channel}

        if channel == "email":
            return await self.notification_service.send_otp_email(
                recipients=recipient,
                otp_code=otp_code,
                purpose=purpose,
                expires_at=expires_at,
                **dict(delivery_context or {}),
            )
        return await self.notification_service.send_otp_sms(
            recipients=recipient,
            otp_code=otp_code,
            purpose=purpose,
            expires_at=expires_at,
            **dict(delivery_context or {}),
        )

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client when available, otherwise None."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("Redis unavailable for OTP operations: %s", exc)
            return None

    def _hash_secret(self, value: str) -> str:
        """Hash a secret value using SHA-256 for secure storage."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _otp_length(self) -> int:
        """Read the configured OTP length from settings or environment variables."""
        return self._read_int_value("OTP_LENGTH", default=getattr(settings, "otp_length", 6))

    def _otp_expiry_minutes(self) -> int:
        """Read the configured OTP expiry from settings or environment variables."""
        return self._read_int_value("OTP_EXPIRY_MINUTES", default=getattr(settings, "otp_expiry_minutes", 10))

    def _max_verification_attempts(self) -> int:
        """Read the configured maximum OTP verification attempts."""
        return self._read_int_value("OTP_MAX_VERIFICATION_ATTEMPTS", default=5)

    def _max_resend_attempts(self) -> int:
        """Read the configured maximum OTP resend attempts."""
        return self._read_int_value("OTP_MAX_RESEND_ATTEMPTS", default=3)

    def _resend_cooldown_seconds(self) -> int:
        """Read the configured resend cooldown interval."""
        return self._read_int_value("OTP_RESEND_COOLDOWN_SECONDS", default=60)

    def _rate_limit_window_seconds(self) -> int:
        """Read the configured rate-limit window."""
        return self._read_int_value("OTP_RATE_LIMIT_WINDOW_SECONDS", default=300)

    def _max_rate_limit_attempts(self) -> int:
        """Read the configured rate-limit maximum."""
        return self._read_int_value("OTP_RATE_LIMIT_MAX_ATTEMPTS", default=5)

    def _read_int_value(self, name: str, *, default: int) -> int:
        """Read an integer configuration value from the settings object or environment."""
        if hasattr(settings, name.lower()):
            value = getattr(settings, name.lower(), None)
            if isinstance(value, int):
                return value
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return int(raw_value)
        except ValueError:
            return default

    def _ttl_seconds(self, expires_at: datetime) -> int:
        """Calculate the Redis TTL in seconds for an OTP payload."""
        return max(int((expires_at - datetime.now(timezone.utc)).total_seconds()), 60)

    def _ttl_seconds_from_payload(self, payload: Mapping[str, Any]) -> int:
        """Calculate the remaining TTL for an OTP payload from its metadata."""
        expires_at = payload.get("expires_at")
        if not expires_at:
            return 60
        try:
            expiry_dt = datetime.fromisoformat(str(expires_at))
        except ValueError:
            return 60
        return max(int((expiry_dt - datetime.now(timezone.utc)).total_seconds()), 60)

    def _otp_key(self, reference_id: str) -> str:
        """Build a Redis key for an OTP payload."""
        return f"{self._OTP_PREFIX}:{reference_id}"

    def _attempt_key(self, identifier: str, purpose: str) -> str:
        """Build a Redis key for OTP verification attempts."""
        return f"{self._ATTEMPT_PREFIX}:{purpose}:{identifier}"

    def _resend_key(self, identifier: str, purpose: str) -> str:
        """Build a Redis key for OTP resend cooldown tracking."""
        return f"{self._RESEND_PREFIX}:{purpose}:{identifier}"

    def _rate_limit_key(self, identifier: str, purpose: str) -> str:
        """Build a Redis key for OTP rate-limit tracking."""
        return f"{self._RATE_LIMIT_PREFIX}:{purpose}:{identifier}"

    def _extract_recipient(self, payload_raw: str | None) -> str:
        """Extract a recipient from a serialized payload when available."""
        if not payload_raw:
            return ""
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            return ""
        return str(payload.get("recipient") or "")

    def _read_bool(self, key: str) -> bool:
        """Read a boolean value from Redis for a simple rate-limit check."""
        return False


__all__ = [
    "OTPService",
    "InvalidOtpException",
    "ExpiredOtpException",
    "OtpAlreadyUsedException",
    "TooManyVerificationAttemptsException",
    "TooManyResendAttemptsException",
    "ResendCooldownActiveException",
    "UnsupportedOtpTypeException",
]
