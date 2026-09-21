from __future__ import annotations

import hashlib
import logging
import os
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from jose import ExpiredSignatureError, JWTError, jwt

from app.config.redis import get_redis
from app.config.settings import settings
from app.utils.exceptions import AuthenticationException, ValidationException
from app.utils.logger import get_logger, log_security_event


class TokenService:
    """Enterprise-grade JWT token service for access and refresh tokens.

    This service is responsible for token issuance, validation, expiration checks,
    issuer and audience verification, token revocation, and refresh-token rotation.
    It intentionally contains no HTTP or controller logic and is designed to be used
    by the higher-level authentication service layer.
    """

    REVOCATION_PREFIX = "jwt:revoked"
    _REVOCATION_PREFIX = REVOCATION_PREFIX
    _FAMILY_PREFIX = "jwt:family"
    _ROTATION_LOCK_PREFIX = "jwt:rotate_lock"

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
    ) -> None:
        """Initialize the token service with injectable dependencies."""
        self.settings = settings_obj or settings
        self.redis_client = redis_client
        self.logger = logger or get_logger(__name__)

    def create_access_token(
        self,
        subject: str,
        *,
        extra_claims: Mapping[str, Any] | None = None,
        ttl: timedelta | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Create a signed access token with standard security claims."""
        if not subject:
            raise ValidationException("A subject is required to issue an access token.")

        claims = self._build_claims(
            subject=subject,
            token_type="access",
            ttl=ttl or timedelta(minutes=self.settings.access_token_expire_minutes),
            extra_claims=extra_claims,
        )
        if device_id:
            claims["device_id"] = device_id
        if session_id:
            claims["session_id"] = session_id
        return self._encode_token(claims)

    def create_refresh_token(
        self,
        subject: str,
        *,
        extra_claims: Mapping[str, Any] | None = None,
        ttl: timedelta | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
        family_id: str | None = None,
    ) -> str:
        """Create a signed refresh token with family tracking for rotation."""
        if not subject:
            raise ValidationException("A subject is required to issue a refresh token.")

        family_identifier = family_id or self.generate_token_family_id()
        claims = self._build_claims(
            subject=subject,
            token_type="refresh",
            ttl=ttl or timedelta(days=self.settings.refresh_token_expire_days),
            extra_claims=extra_claims,
        )
        claims["family_id"] = family_identifier
        claims["token_family_id"] = family_identifier
        if device_id:
            claims["device_id"] = device_id
        if session_id:
            claims["session_id"] = session_id
        return self._encode_token(claims)

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Decode and validate an access token."""
        return self.validate_token(token, expected_type="access")

    def decode_refresh_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a refresh token."""
        return self.validate_token(token, expected_type="refresh")

    def validate_token(self, token: str, *, expected_type: str | None = None) -> dict[str, Any]:
        """Decode, validate, and verify a JWT token payload."""
        if not token:
            raise ValidationException("A token is required for validation.")

        claims = self._decode_token(token)
        self.verify_token_type(claims, expected_type=expected_type)
        self.verify_expiration(claims)
        self.verify_issuer(claims)
        self.verify_audience(claims)
        return claims

    def verify_token_type(self, claims: Mapping[str, Any], *, expected_type: str | None = None) -> dict[str, Any]:
        """Ensure the token type matches the expected token category."""
        token_type = claims.get("type")
        if expected_type and token_type != expected_type:
            raise AuthenticationException(
                detail=f"Unexpected token type: {token_type or 'unknown'}.",
                error_code="INVALID_TOKEN_TYPE",
            )
        return dict(claims)

    def verify_expiration(self, claims: Mapping[str, Any]) -> dict[str, Any]:
        """Verify the expiration timestamp and reject expired tokens."""
        expiration = claims.get("exp")
        if expiration is None:
            raise AuthenticationException(detail="Token is missing an expiration claim.", error_code="TOKEN_EXPIRED")

        if isinstance(expiration, datetime):
            expires_at = expiration
        else:
            expires_at = datetime.fromtimestamp(int(expiration), tz=timezone.utc)

        now = datetime.now(timezone.utc)
        if expires_at <= now:
            log_security_event(self.logger, "Expired token rejected", token_type=claims.get("type"), subject=claims.get("sub"))
            raise AuthenticationException(detail="Token has expired.", error_code="TOKEN_EXPIRED")

        return dict(claims)

    def verify_issuer(self, claims: Mapping[str, Any]) -> dict[str, Any]:
        """Verify that the token issuer matches the configured issuer when present."""
        issuer = self._configured_issuer()
        if issuer:
            token_issuer = claims.get("iss")
            if token_issuer != issuer:
                log_security_event(self.logger, "Token issuer verification failed", subject=claims.get("sub"), expected_issuer=issuer)
                raise AuthenticationException(detail="Invalid token issuer.", error_code="INVALID_TOKEN_ISSUER")
        return dict(claims)

    def verify_audience(self, claims: Mapping[str, Any]) -> dict[str, Any]:
        """Verify the audience claim when the service has a configured audience."""
        audience = self._configured_audience()
        if audience:
            token_audience = claims.get("aud")
            if token_audience is None:
                raise AuthenticationException(detail="Token is missing an audience claim.", error_code="INVALID_TOKEN_AUDIENCE")
            if isinstance(token_audience, (list, tuple, set)):
                if audience not in token_audience:
                    raise AuthenticationException(detail="Token audience is invalid.", error_code="INVALID_TOKEN_AUDIENCE")
            elif token_audience != audience:
                raise AuthenticationException(detail="Token audience is invalid.", error_code="INVALID_TOKEN_AUDIENCE")
        return dict(claims)

    def generate_jti(self) -> str:
        """Generate a unique JWT identifier for token tracing and revocation."""
        return uuid4().hex

    def generate_token_family_id(self) -> str:
        """Generate a stable family identifier for refresh token rotation."""
        return uuid4().hex

    def generate_secure_random_token_id(self) -> str:
        """Create a cryptographically strong random token identifier."""
        return secrets.token_urlsafe(24)

    async def revoke_token(self, token: str, *, reason: str | None = None) -> dict[str, Any]:
        """Revoke a token by storing its JWT ID in Redis with a TTL."""
        claims = self.validate_token(token)
        jti = str(claims.get("jti") or self.generate_jti())
        token_type = str(claims.get("type") or "unknown")
        ttl_seconds = self._calculate_ttl_seconds(claims)

        redis_client = await self._get_redis_client()
        if redis_client is None:
            log_security_event(self.logger, "Token revocation skipped because Redis is unavailable", jti=jti, token_type=token_type)
            return {"revoked": False, "jti": jti, "reason": reason or "redis_unavailable"}

        key = self._revocation_key(jti)
        await redis_client.set(key, reason or "revoked", ex=max(ttl_seconds, 60))
        log_security_event(self.logger, "Token revoked", jti=jti, token_type=token_type, reason=reason or "revoked")
        return {"revoked": True, "jti": jti, "reason": reason or "revoked"}

    async def check_revoked_token(self, token: str | None = None, *, jti: str | None = None) -> bool:
        """Return True if the supplied token or JWT ID has been revoked."""
        if not jti and token:
            claims = self.validate_token(token)
            jti = str(claims.get("jti") or "")
        if not jti:
            return False

        redis_client = await self._get_redis_client()
        if redis_client is None:
            return False

        return bool(await redis_client.exists(self._revocation_key(jti)))

    async def rotate_refresh_token(
        self,
        refresh_token: str,
        *,
        subject: str | None = None,
        device_id: str | None = None,
        session_id: str | None = None,
        family_id: str | None = None,
    ) -> dict[str, Any]:
        """Rotate a refresh token by revoking the old token and issuing a new one."""
        claims = self.validate_token(refresh_token, expected_type="refresh")
        redis_client = await self._get_redis_client()
        lock_key = self._rotation_lock_key(str(claims.get("jti") or ""))
        lock_acquired = False
        try:
            if redis_client is not None and lock_key:
                lock_acquired = bool(await redis_client.set(lock_key, "1", ex=30, nx=True))
                if not lock_acquired:
                    raise AuthenticationException(detail="Refresh token has been revoked.", error_code="TOKEN_REVOKED")

            if await self.check_revoked_token(token=refresh_token):
                raise AuthenticationException(detail="Refresh token has been revoked.", error_code="TOKEN_REVOKED")

            new_family_id = family_id or str(claims.get("family_id") or claims.get("token_family_id") or self.generate_token_family_id())
            new_token = self.create_refresh_token(
                subject=subject or str(claims.get("sub") or ""),
                device_id=device_id or claims.get("device_id"),
                session_id=session_id or claims.get("session_id"),
                family_id=new_family_id,
            )
            await self.revoke_token(refresh_token, reason="rotated")

            if redis_client is not None:
                family_key = self._family_key(new_family_id)
                await redis_client.sadd(family_key, str(claims.get("jti") or ""), str(self.extract_claims(new_token).get("jti") or ""))
                await redis_client.expire(family_key, max(self._calculate_ttl_seconds(claims), 60))

            return {
                "refresh_token": new_token,
                "claims": self.extract_claims(new_token),
                "family_id": new_family_id,
                "revoked_previous": True,
            }
        finally:
            if redis_client is not None and lock_key and lock_acquired:
                await redis_client.delete(lock_key)

    def extract_user_id(self, token_or_claims: str | Mapping[str, Any]) -> str | None:
        """Extract the user subject identifier from a token or claims mapping."""
        claims = self.extract_claims(token_or_claims)
        return str(claims.get("sub") or "") or None

    def extract_claims(self, token_or_claims: str | Mapping[str, Any]) -> dict[str, Any]:
        """Extract and return claims from a JWT string or a claims mapping."""
        if isinstance(token_or_claims, str):
            try:
                return self.validate_token(token_or_claims)
            except (AuthenticationException, ValidationException, JWTError, ValueError):
                return {}
        if isinstance(token_or_claims, Mapping):
            return dict(token_or_claims)
        return {}

    def extract_device_id(self, token_or_claims: str | Mapping[str, Any]) -> str | None:
        """Extract the device ID from a token or claims mapping when present."""
        claims = self.extract_claims(token_or_claims)
        value = claims.get("device_id")
        return str(value) if value is not None else None

    def extract_session_id(self, token_or_claims: str | Mapping[str, Any]) -> str | None:
        """Extract the session ID from a token or claims mapping when present."""
        claims = self.extract_claims(token_or_claims)
        value = claims.get("session_id")
        return str(value) if value is not None else None

    def _build_claims(
        self,
        *,
        subject: str,
        token_type: str,
        ttl: timedelta,
        extra_claims: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a standard JWT claim set with security attributes."""
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + ttl
        claims: dict[str, Any] = {
            "sub": subject,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "nbf": int(issued_at.timestamp()),
            "iss": self._configured_issuer() or "cosmozpay",
            "jti": self.generate_jti(),
            "type": token_type,
        }

        if self._configured_audience():
            claims["aud"] = self._configured_audience()

        if extra_claims:
            for key, value in extra_claims.items():
                if value is not None:
                    claims[key] = value
        return claims

    def _encode_token(self, claims: Mapping[str, Any]) -> str:
        """Encode and sign a JWT payload using the configured algorithm."""
        payload = dict(claims)
        return jwt.encode(payload, self._get_signing_key(), algorithm=self._get_algorithm())

    def _decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT using the configured signing key and settings."""
        try:
            return jwt.decode(
                token,
                self._get_signing_key(),
                algorithms=[self._get_algorithm()],
                issuer=self._configured_issuer() or None,
                audience=self._configured_audience() or None,
                options={"verify_signature": True, "verify_exp": True, "verify_nbf": True},
            )
        except ExpiredSignatureError as exc:
            raise AuthenticationException(detail="Token has expired.", error_code="TOKEN_EXPIRED") from exc
        except JWTError as exc:
            raise AuthenticationException(detail="Invalid token.", error_code="INVALID_TOKEN") from exc

    def _get_signing_key(self) -> str:
        """Return the configured signing key for the active algorithm."""
        secret_value = None
        if getattr(self.settings, "jwt_secret_key", None) is not None:
            secret_value = getattr(self.settings.jwt_secret_key, "get_secret_value", lambda: str(self.settings.jwt_secret_key))()
        if not secret_value:
            secret_value = os.getenv("JWT_SECRET_KEY")
        if not secret_value:
            raise RuntimeError("JWT secret key is not configured")

        # If RS256 is configured, require a PEM-formatted key to avoid accidental
        # use of an ordinary secret string as an RSA key. This prevents misconfiguration
        # where a symmetric secret is used with an asymmetric algorithm.
        algorithm = self._get_algorithm()
        if algorithm == "RS256":
            pem_markers = ("-----BEGIN PRIVATE KEY-----", "-----BEGIN PUBLIC KEY-----", "-----BEGIN RSA PRIVATE KEY-----")
            if not any(marker in str(secret_value) for marker in pem_markers):
                raise ValidationException(detail="RS256 requires a PEM-formatted RSA key configured in JWT_SECRET_KEY.", error_code="INVALID_JWT_KEY")
        return str(secret_value)

    def _get_algorithm(self) -> str:
        """Return the configured JWT algorithm, defaulting to HS256."""
        algorithm = str(getattr(self.settings, "jwt_algorithm", "HS256") or "HS256").upper()
        if algorithm not in {"HS256", "RS256"}:
            raise ValidationException(detail=f"Unsupported JWT algorithm: {algorithm}", error_code="INVALID_JWT_ALGORITHM")
        return algorithm

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client when available, otherwise None."""
        if self.redis_client is not None:
            return self.redis_client
        try:
            return await get_redis()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("Redis unavailable for token operations: %s", exc)
            return None

    def _configured_issuer(self) -> str | None:
        """Return the configured issuer name if one is available."""
        issuer = getattr(self.settings, "jwt_issuer", None)
        if issuer:
            return str(issuer)
        return os.getenv("JWT_ISSUER")

    def _configured_audience(self) -> str | None:
        """Return the configured audience name if one is available."""
        audience = getattr(self.settings, "jwt_audience", None)
        if audience:
            return str(audience)
        return os.getenv("JWT_AUDIENCE")

    def _revocation_key(self, jti: str) -> str:
        """Build a Redis key for revoked token state."""
        return f"{self._REVOCATION_PREFIX}:{jti}"

    def _family_key(self, family_id: str) -> str:
        """Build a Redis key for token-family tracking."""
        return f"{self._FAMILY_PREFIX}:{family_id}"

    def _rotation_lock_key(self, jti: str) -> str:
        """Build a short-lived Redis lock key for refresh-token rotation."""
        if not jti:
            return ""
        return f"{self._ROTATION_LOCK_PREFIX}:{jti}"

    def _calculate_ttl_seconds(self, claims: Mapping[str, Any]) -> int:
        """Calculate a reasonable Redis TTL based on the token expiration claim."""
        expiration = claims.get("exp")
        if expiration is None:
            return 300
        try:
            expires_at = datetime.fromtimestamp(int(expiration), tz=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(int((expires_at - now).total_seconds()), 60)
        except (TypeError, ValueError):
            return 300


__all__ = ["TokenService"]
