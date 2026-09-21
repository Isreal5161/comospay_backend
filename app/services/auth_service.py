from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.jwt import create_access_token, create_refresh_token, decode_token
from app.config.settings import settings
from app.config.security import (
    generate_otp,
    hash_password,
    hash_pin,
    normalize_phone_number,
    validate_email,
    validate_password_strength,
    validate_pin,
    verify_password,
    verify_pin,
)
from app.models.device import Device
from app.models.otp import OTP
from app.models.user import User
from app.models.wallet import Wallet
from app.services.auth.device_service import DeviceService
from app.services.auth.otp_service import (
    OTPService,
    ResendCooldownActiveException,
    TooManyResendAttemptsException,
)
from app.services.auth.password_service import (
    ExpiredResetTokenException,
    InvalidResetTokenException,
    PasswordPolicyViolationException,
    PasswordReuseViolationException,
    PasswordService,
)
from app.services.auth.session_service import (
    SessionExpiredException,
    SessionNotFoundException,
    SessionRevokedException,
    SessionService,
)
from app.services.auth.token_service import TokenService
from app.utils.exceptions import (
    AuthenticationException,
    DatabaseException,
    OTPException,
    ValidationException,
)


class _DefaultJwtUtils:
    """Small adapter that exposes the configured JWT helpers through an injectable interface."""

    def create_access_token(self, subject: str, extra_claims: dict[str, Any] | None = None) -> str:
        return create_access_token(subject, extra_claims=extra_claims)

    def create_refresh_token(self, subject: str, extra_claims: dict[str, Any] | None = None) -> str:
        return create_refresh_token(subject, extra_claims=extra_claims)

    def decode_token(self, token: str) -> dict[str, Any]:
        return decode_token(token)


class _DefaultPasswordUtils:
    """Adapter around the password and security helpers used by the auth service."""

    def validate_password_strength(self, password: str) -> bool:
        return validate_password_strength(password)

    def hash_password(self, password: str) -> str:
        return hash_password(password)

    def verify_password(self, password: str, hashed_password: str) -> bool:
        return verify_password(password, hashed_password)

    def hash_pin(self, pin: str) -> str:
        return hash_pin(pin)

    def verify_pin(self, pin: str, hashed_pin: str) -> bool:
        return verify_pin(pin, hashed_pin)

    def validate_pin(self, pin: str, length: int | None = None) -> bool:
        return validate_pin(pin, length=length)

    def normalize_phone_number(self, phone: str) -> str:
        return normalize_phone_number(phone)

    def validate_email(self, email: str) -> bool:
        return validate_email(email)

    def generate_otp(self, length: int | None = None) -> str:
        return generate_otp(length=length)

    def _password_service(self) -> PasswordService:
        return PasswordService()

    async def generate_password_reset_token(self, *, user_id: str, ttl_minutes: int | None = None) -> str:
        return await self._password_service().generate_password_reset_token(user_id=user_id, ttl_minutes=ttl_minutes)

    async def verify_password_reset_token(self, token: str) -> dict[str, Any]:
        return await self._password_service().verify_password_reset_token(token)

    async def enforce_password_policy(
        self,
        *,
        password: str,
        confirmation: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        return await self._password_service().enforce_password_policy(
            password=password,
            confirmation=confirmation,
            user_id=user_id,
        )

    async def store_password_history(self, *, user_id: str, password_hash: str) -> None:
        await self._password_service().store_password_history(user_id=user_id, password_hash=password_hash)

    async def invalidate_password_reset_token(self, token: str) -> bool:
        return await self._password_service().invalidate_password_reset_token(token)


class AuthService:
    """Coordinate authentication workflows for users, devices, tokens, and OTPs."""

    def __init__(
        self,
        *,
        user_repository: Any = None,
        wallet_repository: Any = None,
        otp_repository: Any = None,
        device_repository: Any = None,
        session: Any = None,
        jwt_utils: Any = None,
        password_utils: Any = None,
        redis_client: Any = None,
        notification_service: Any = None,
        password_service: Any = None,
        otp_service: Any = None,
        token_service: Any = None,
        session_service: Any = None,
        device_service: Any = None,
        wallet_service: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.otp_repository = otp_repository
        self.device_repository = device_repository
        self.session = session
        self.jwt_utils = jwt_utils or _DefaultJwtUtils()
        self.password_utils = password_utils or _DefaultPasswordUtils()
        self.password_service = password_service
        self.otp_service = otp_service
        self.token_service = token_service
        self.session_service = session_service
        self.device_service = device_service
        self.wallet_service = wallet_service
        self.redis_client = redis_client
        self.notification_service = notification_service
        self.logger = logger or logging.getLogger(__name__)
        self.audit_hook: Callable[..., Any] | None = None

    async def register_user(
        self,
        *,
        email: str,
        password: str,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        username: str | None = None,
        device_fingerprint: str | None = None,
        ip_address: str | None = None,
        device_name: str | None = None,
        device_type: str = "unknown",
    ) -> dict[str, Any]:
        """Create a new user account, a wallet, and optional device record."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)
        self._require_repository("wallet_repository", self.wallet_repository)

        if not email or not password:
            raise ValidationException("Email and password are required.")
        if not self.password_utils.validate_email(email):
            raise ValidationException("Email address is invalid.")
        if not self.password_utils.validate_password_strength(password):
            raise ValidationException("Password does not meet the required strength.")

        normalized_email = email.strip().lower()
        normalized_phone = self._normalize_phone(phone)

        if self.user_repository is not None and await self.user_repository.get_by_email(normalized_email):
            raise ValidationException("A user with this email already exists.")
        if normalized_phone and self.user_repository is not None and await self.user_repository.get_by_phone(normalized_phone):
            raise ValidationException("A user with this phone number already exists.")

        try:
            async with self.session.begin():
                user = User(
                    first_name=first_name.strip() if first_name else None,
                    last_name=last_name.strip() if last_name else None,
                    email=normalized_email,
                    phone=normalized_phone,
                    username=username.strip().lower() if username else None,
                    password_hash=self.password_utils.hash_password(password),
                    status="pending",
                    is_active=True,
                    is_blocked=False,
                    is_suspended=False,
                    email_verified=False,
                    phone_verified=False,
                )
                if self.user_repository is not None:
                    await self.user_repository.create_user(user)

                wallet = Wallet(
                    user_id=user.id,
                    wallet_reference=f"wallet-{user.id.hex[:8]}",
                    wallet_type="customer",
                    currency="NGN",
                    status="active",
                    available_balance=0,
                    ledger_balance=0,
                    locked_balance=0,
                    is_active=True,
                    is_frozen=False,
                    is_suspended=False,
                )
                if self.wallet_repository is not None:
                    await self.wallet_repository.create_wallet(wallet)

                if device_fingerprint:
                    await self._register_or_update_device(
                        user_id=user.id,
                        device_fingerprint=device_fingerprint,
                        ip_address=ip_address,
                        device_name=device_name,
                        device_type=device_type,
                        is_trusted=True,
                    )

                await self._log_event("user_registered", user_id=user.id)
                return {
                    "user": self._serialize_user(user),
                    "access_token": self._issue_access_token(user),
                    "token_type": "bearer",
                }
        except Exception as exc:
            raise DatabaseException("Registration failed.") from exc

    async def login(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        password: str,
        device_fingerprint: str | None = None,
        ip_address: str | None = None,
        device_name: str | None = None,
        device_type: str = "unknown",
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate a user and issue secure access and refresh tokens."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not password:
            raise ValidationException("Password is required.")
        identifier = email or phone
        if not identifier:
            raise ValidationException("Email or phone number is required.")

        normalized_email = email.strip().lower() if email else None
        normalized_phone = self._normalize_phone(phone) if phone else None
        user = await self._get_user_by_identifier(email=normalized_email, phone=normalized_phone)
        if not user or not user.password_hash:
            await self._log_event("login_failed", user_id=None, metadata={"reason": "invalid_credentials"})
            raise AuthenticationException("Invalid credentials.")

        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")
        if user.account_locked_until and user.account_locked_until > datetime.now(timezone.utc):
            raise AuthenticationException("Account is temporarily locked.")

        require_email_verification = bool(getattr(settings, "require_email_verification", False))
        if require_email_verification and not user.email_verified:
            raise ValidationException("Email verification is required.")

        password_service = self.password_service or self.password_utils
        if not password_service.verify_password(password, user.password_hash):
            await self._handle_failed_login(user)
            raise AuthenticationException("Invalid credentials.")

        if getattr(user, "mfa_enabled", False) and self.otp_service is not None:
            try:
                otp_result = await self.otp_service.send_otp(
                    recipient=user.email,
                    purpose="login_verification",
                    channel="email",
                    user_id=str(user.id),
                    metadata={"user_id": str(user.id), "email": user.email},
                    delivery_context={"subject": "Verify your login", "template_name": "mfa_login"},
                )
            except Exception as exc:
                raise DatabaseException("MFA verification could not be started.") from exc
            await self._log_event("login_mfa_required", user_id=user.id, metadata={"email": user.email})
            return {
                "requires_mfa": True,
                "message": "Multi-factor verification is required.",
                "reference_id": otp_result.get("reference_id"),
            }

        try:
            async with self.session.begin():
                locked_user = await self._get_user_for_update(user.id)
                if locked_user is None or not locked_user.password_hash:
                    raise AuthenticationException("Invalid credentials.")
                if locked_user.account_locked_until and locked_user.account_locked_until > datetime.now(timezone.utc):
                    raise AuthenticationException("Account is temporarily locked.")

                locked_user.failed_login_attempts = 0
                locked_user.account_locked_until = None
                locked_user.last_login_at = datetime.now(timezone.utc)
                if locked_user.status in {"pending", "locked"}:
                    locked_user.status = "active"
                await self.user_repository.update_user(
                    locked_user,
                    failed_login_attempts=locked_user.failed_login_attempts,
                    account_locked_until=locked_user.account_locked_until,
                    last_login_at=locked_user.last_login_at,
                    status=locked_user.status,
                )
                user = locked_user

            device_id: str | None = None
            session_id: str | None = None
            session_payload: dict[str, Any] | None = None
            refresh_token_family_id = None
            access_token = ""
            refresh_token = ""

            if device_fingerprint:
                if self.device_service is not None:
                    try:
                        device_payload = await self.device_service.register_device(
                            user_id=str(user.id),
                            device_fingerprint=device_fingerprint,
                            device_name=device_name,
                            device_type=device_type,
                            ip_address=ip_address,
                            user_agent=user_agent,
                            is_trusted=True,
                        )
                        device_id = str(device_payload.get("device_id") or "") or None
                    except Exception as exc:
                        self.logger.warning("device_registration_failed", extra={"user_id": str(user.id), "error": str(exc)})
                else:
                    device = await self._register_or_update_device(
                        user_id=user.id,
                        device_fingerprint=device_fingerprint,
                        ip_address=ip_address,
                        device_name=device_name,
                        device_type=device_type,
                        is_trusted=True,
                    )
                    device_id = str(device.id) if getattr(device, "id", None) is not None else None

            if self.token_service is not None and hasattr(self.token_service, "generate_token_family_id"):
                refresh_token_family_id = self.token_service.generate_token_family_id()

            if self.session_service is not None:
                session_payload = await self.session_service.create_session(
                    user_id=str(user.id),
                    metadata={
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                        "device_fingerprint": device_fingerprint,
                        "login_method": "password",
                    },
                    refresh_token_family_id=refresh_token_family_id,
                )
                if session_payload is not None:
                    session_id = str(session_payload.get("session_id") or "") or None
                if session_id:
                    await self.session_service.update_session(
                        session_id=session_id,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        device_id=device_id,
                        metadata={
                            "ip_address": ip_address,
                            "user_agent": user_agent,
                            "device_fingerprint": device_fingerprint,
                            "login_method": "password",
                        },
                    )

            if self.token_service is not None:
                access_token = self.token_service.create_access_token(
                    str(user.id),
                    extra_claims={"email": user.email, "user_id": str(user.id), "email_verified": user.email_verified, "role": getattr(user, "role", None) or "user"},
                    device_id=device_id,
                    session_id=session_id,
                )
                refresh_token = self.token_service.create_refresh_token(
                    str(user.id),
                    extra_claims={"email": user.email, "user_id": str(user.id)},
                    device_id=device_id,
                    session_id=session_id,
                    family_id=refresh_token_family_id,
                )
            else:
                access_token = self._issue_access_token(user)
                refresh_token = self._issue_refresh_token(user)

            await self._cache_token(refresh_token, token_type="refresh", user_id=user.id)
            await self._log_event(
                "login_succeeded",
                user_id=user.id,
                metadata={
                    "session_id": session_id,
                    "device_id": device_id,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                },
            )
            return {
                "user": self._serialize_user(user),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": settings.access_token_expire_minutes * 60,
                "session_id": session_id,
            }
        except Exception as exc:
            raise DatabaseException("Login failed.") from exc

    async def refresh_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Issue a new access token and optionally rotate the refresh token."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not refresh_token:
            raise ValidationException("Refresh token is required.")

        try:
            if self.token_service is not None and hasattr(self.token_service, "validate_token"):
                claims = self.token_service.validate_token(refresh_token, expected_type="refresh")
            else:
                claims = self.jwt_utils.decode_token(refresh_token)
        except (AuthenticationException, ValidationException, ValueError) as exc:
            raise AuthenticationException("Refresh token is invalid.") from exc

        if claims.get("type") != "refresh":
            raise AuthenticationException("Refresh token is invalid.")
        if not claims.get("jti"):
            raise AuthenticationException("Refresh token is invalid.")
        if not (claims.get("family_id") or claims.get("token_family_id")):
            raise AuthenticationException("Refresh token family is invalid.")

        if self.token_service is not None and hasattr(self.token_service, "check_revoked_token"):
            try:
                if await self.token_service.check_revoked_token(token=refresh_token):
                    raise AuthenticationException("Refresh token has been revoked.")
            except AuthenticationException:
                raise
            except Exception as exc:
                raise DatabaseException("Token refresh failed.") from exc
        elif await self._is_token_revoked(refresh_token):
            raise AuthenticationException("Refresh token has been revoked.")

        user_id = claims.get("sub")
        user = await self.user_repository.get_by_id(UUID(str(user_id))) if user_id and self.user_repository is not None else None
        if not user:
            raise AuthenticationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        session_id = claims.get("session_id")
        if session_id and self.session_service is not None:
            try:
                await self.session_service.validate_session(session_id=session_id)
            except Exception as exc:
                raise AuthenticationException("Session is invalid.") from exc

        device_id = claims.get("device_id")
        if device_id and self.device_service is not None:
            try:
                device_payload = await self.device_service.retrieve_device(device_id=device_id)
            except Exception as exc:
                raise AuthenticationException("Device is invalid.") from exc
            if not bool(device_payload.get("is_trusted", False)):
                raise AuthenticationException("Device is not trusted.")

        rotation_enabled = bool(getattr(settings, "refresh_token_rotation_enabled", True))
        access_token = ""
        new_refresh_token = None

        try:
            if self.token_service is not None:
                access_token = self.token_service.create_access_token(
                    str(user.id),
                    extra_claims={"email": user.email, "user_id": str(user.id), "email_verified": user.email_verified, "role": getattr(user, "role", None) or "user"},
                    device_id=device_id,
                    session_id=session_id,
                )
                if rotation_enabled and hasattr(self.token_service, "rotate_refresh_token"):
                    rotation_result = await self.token_service.rotate_refresh_token(
                        refresh_token,
                        subject=str(user.id),
                        device_id=device_id,
                        session_id=session_id,
                        family_id=claims.get("family_id") or claims.get("token_family_id"),
                    )
                    new_refresh_token = rotation_result.get("refresh_token")
                else:
                    await self._revoke_token(refresh_token, token_type="refresh")
                    new_refresh_token = self.token_service.create_refresh_token(
                        str(user.id),
                        extra_claims={"email": user.email, "user_id": str(user.id)},
                        device_id=device_id,
                        session_id=session_id,
                        family_id=claims.get("family_id") or claims.get("token_family_id"),
                    )
            else:
                access_token = self._issue_access_token(user)
                await self._revoke_token(refresh_token, token_type="refresh")
                new_refresh_token = self._issue_refresh_token(user)

            if new_refresh_token:
                await self._cache_token(new_refresh_token, token_type="refresh", user_id=user.id)

            if session_id and self.session_service is not None:
                try:
                    await self.session_service.update_session(session_id=session_id)
                except Exception:
                    pass

            await self._log_event(
                "token_refreshed",
                user_id=user.id,
                metadata={
                    "session_id": session_id,
                    "device_id": device_id,
                    "rotation_enabled": rotation_enabled,
                },
            )
            response: dict[str, Any] = {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": settings.access_token_expire_minutes * 60,
            }
            if new_refresh_token:
                response["refresh_token"] = new_refresh_token
            if session_id:
                response["session_id"] = session_id
            return response
        except AuthenticationException:
            if session_id and self.session_service is not None:
                try:
                    await self.session_service.revoke_session(session_id=session_id, reason="token_reuse")
                except Exception:
                    pass
            raise
        except Exception as exc:
            raise DatabaseException("Token refresh failed.") from exc

    async def logout(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        session_id: str | None = None,
        device_id: str | None = None,
        user_id: UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Revoke the supplied tokens, terminate the associated session, and update device state."""
        if not access_token and not refresh_token and not session_id and not device_id and user_id is None:
            raise ValidationException("At least one logout context is required.")

        claims: dict[str, Any] = {}
        if access_token:
            claims = await self._decode_token_claims(access_token, expected_type="access")
        elif refresh_token:
            claims = await self._decode_token_claims(refresh_token, expected_type="refresh")

        session_id = session_id or (str(claims.get("session_id")) if claims.get("session_id") is not None else None)
        device_id = device_id or (str(claims.get("device_id")) if claims.get("device_id") is not None else None)

        resolved_user_id = user_id or self._extract_user_id_from_claims(claims)
        if resolved_user_id is not None:
            resolved_user_id = UUID(str(resolved_user_id))

        if self.token_service is not None:
            if access_token:
                try:
                    await self.token_service.revoke_token(access_token, reason="logout")
                except Exception:
                    await self._revoke_token(access_token, token_type="access")
            if refresh_token:
                try:
                    await self.token_service.revoke_token(refresh_token, reason="logout")
                except Exception:
                    await self._revoke_token(refresh_token, token_type="refresh")
                if self.token_service is not None and hasattr(self.token_service, "redis_client"):
                    await self._mark_refresh_family_revoked(claims)
        else:
            if access_token:
                await self._revoke_token(access_token, token_type="access")
            if refresh_token:
                await self._revoke_token(refresh_token, token_type="refresh")

        if session_id and self.session_service is not None:
            try:
                await self.session_service.revoke_session(session_id=session_id, reason="logout")
            except Exception as exc:
                raise AuthenticationException("Session is invalid.") from exc

        if device_id and self.device_service is not None:
            try:
                await self.device_service.retrieve_device(device_id=device_id)
                await self.device_service.update_device_information(
                    device_id=device_id,
                    last_seen_at=datetime.now(timezone.utc).isoformat(),
                    metadata={
                        "last_logout_at": datetime.now(timezone.utc).isoformat(),
                        "logout_ip_address": ip_address,
                        "logout_user_agent": user_agent,
                    },
                )
            except Exception as exc:
                raise ValidationException("Device not found.") from exc

        if resolved_user_id is not None and self.user_repository is not None:
            try:
                user = await self.user_repository.get_by_id(resolved_user_id)
                if user is not None:
                    await self._log_event(
                        "user_logged_out",
                        user_id=user.id,
                        metadata={
                            "session_id": session_id,
                            "device_id": device_id,
                            "ip_address": ip_address,
                            "user_agent": user_agent,
                        },
                    )
                    return {"message": "Logged out successfully."}
            except Exception:
                pass

        await self._log_event(
            "user_logged_out",
            user_id=resolved_user_id,
            metadata={
                "session_id": session_id,
                "device_id": device_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )
        return {"message": "Logged out successfully."}

    async def get_active_sessions(self, *, user_id: UUID, current_session_id: str | None = None) -> dict[str, Any]:
        """Return the active sessions belonging to the authenticated user."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        if self.session_service is None:
            raise DatabaseException("Session service is unavailable.")

        try:
            session_payloads = await self.session_service.get_active_sessions(user_id=str(user.id))
        except Exception as exc:
            raise DatabaseException("Unable to retrieve active sessions.") from exc

        sessions: list[dict[str, Any]] = []
        for session_payload in session_payloads:
            sessions.append(await self._serialize_session(session_payload, current_session_id=current_session_id))

        await self._log_event(
            "active_sessions_viewed",
            user_id=user.id,
            metadata={"count": len(sessions), "current_session_id": current_session_id},
        )
        return {"sessions": sessions, "count": len(sessions)}

    async def get_session_details(self, *, user_id: UUID, session_id: str) -> dict[str, Any]:
        """Return the details of a specific session after validating ownership."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not session_id:
            raise ValidationException("Session ID is required.")

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        if self.session_service is None:
            raise DatabaseException("Session service is unavailable.")

        try:
            session_payload = await self.session_service.retrieve_session(session_id=session_id)
        except SessionNotFoundException as exc:
            raise AuthenticationException("Session not found.") from exc
        except (SessionExpiredException, SessionRevokedException) as exc:
            raise AuthenticationException("Session is invalid.") from exc
        except Exception as exc:
            raise DatabaseException("Unable to retrieve session details.") from exc

        if str(session_payload.get("user_id") or "") != str(user.id):
            raise AuthenticationException("You are not authorized to view this session.")

        await self._log_event("session_details_viewed", user_id=user.id, metadata={"session_id": session_id})
        return {"session": await self._serialize_session(session_payload, current_session_id=session_id)}

    async def revoke_session(
        self,
        *,
        user_id: UUID,
        session_id: str,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> dict[str, Any]:
        """Revoke one specific session and its associated security tokens when available."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not session_id:
            raise ValidationException("Session ID is required.")

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        if self.session_service is None:
            raise DatabaseException("Session service is unavailable.")

        try:
            session_payload = await self.session_service.retrieve_session(session_id=session_id)
        except SessionNotFoundException as exc:
            raise AuthenticationException("Session not found.") from exc
        except (SessionExpiredException, SessionRevokedException) as exc:
            raise AuthenticationException("Session is invalid.") from exc
        except Exception as exc:
            raise DatabaseException("Unable to revoke session.") from exc

        if str(session_payload.get("user_id") or "") != str(user.id):
            raise AuthenticationException("You are not authorized to revoke this session.")

        if session_payload.get("status") == "revoked":
            raise AuthenticationException("Session has already been revoked.")

        if access_token:
            try:
                if self.token_service is not None and hasattr(self.token_service, "revoke_token"):
                    await self.token_service.revoke_token(access_token, reason="session_revoke")
                else:
                    await self._revoke_token(access_token, token_type="access")
            except Exception:
                pass

        if refresh_token:
            try:
                if self.token_service is not None and hasattr(self.token_service, "revoke_token"):
                    await self.token_service.revoke_token(refresh_token, reason="session_revoke")
                else:
                    await self._revoke_token(refresh_token, token_type="refresh")
            except Exception:
                pass

        family_id = session_payload.get("refresh_token_family_id") or session_payload.get("family_id")
        if family_id and self.token_service is not None and hasattr(self.token_service, "redis_client"):
            try:
                await self._mark_refresh_family_revoked({"family_id": family_id})
            except Exception:
                pass

        try:
            await self.session_service.revoke_session(session_id=session_id, reason="user_revoke")
        except Exception as exc:
            raise DatabaseException("Unable to revoke session.") from exc

        await self._log_event(
            "session_revoked",
            user_id=user.id,
            metadata={"session_id": session_id, "access_token_provided": bool(access_token), "refresh_token_provided": bool(refresh_token)},
        )
        return {"message": "Session revoked successfully.", "session_id": session_id}

    async def logout_all_sessions(self, *, user_id: UUID, reason: str | None = None, preserve_current: bool = False) -> dict[str, Any]:
        """Revoke all active sessions and associated tokens for the authenticated user."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not user_id:
            raise ValidationException("User ID is required.")

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        if self.session_service is None:
            raise DatabaseException("Session service is unavailable.")

        try:
            session_payloads = await self.session_service.get_active_sessions(user_id=str(user.id))
        except Exception as exc:
            raise DatabaseException("Unable to retrieve active sessions.") from exc

        if not session_payloads:
            await self._log_event("logout_all_sessions_completed", user_id=user.id, metadata={"reason": reason or "none", "revoked_count": 0})
            return {"message": "No active sessions to revoke.", "revoked_count": 0}

        revoked_count = 0
        for session_payload in session_payloads:
            session_id = session_payload.get("session_id") or session_payload.get("id")
            if not session_id:
                continue
            if preserve_current and str(session_id) == str(session_payload.get("current_session_id") or ""):
                continue

            try:
                if self.token_service is not None and hasattr(self.token_service, "revoke_token"):
                    refresh_token = session_payload.get("refresh_token")
                    if refresh_token:
                        await self.token_service.revoke_token(str(refresh_token), reason=reason or "logout_all_sessions")
                    access_token = session_payload.get("access_token")
                    if access_token:
                        await self.token_service.revoke_token(str(access_token), reason=reason or "logout_all_sessions")
                else:
                    refresh_token = session_payload.get("refresh_token")
                    if refresh_token:
                        await self._revoke_token(str(refresh_token), token_type="refresh")
                    access_token = session_payload.get("access_token")
                    if access_token:
                        await self._revoke_token(str(access_token), token_type="access")
            except Exception:
                pass

            try:
                family_id = session_payload.get("refresh_token_family_id") or session_payload.get("family_id")
                if family_id and self.token_service is not None and hasattr(self.token_service, "redis_client"):
                    await self._mark_refresh_family_revoked({"family_id": family_id})
            except Exception:
                pass

            try:
                await self.session_service.revoke_session(session_id=str(session_id), reason=reason or "logout_all_sessions")
            except Exception:
                pass
            else:
                revoked_count += 1

        if self.redis_client is not None and hasattr(self.redis_client, "scan_iter"):
            try:
                async for key in self.redis_client.scan_iter(match="auth:*"):
                    if not isinstance(key, str):
                        continue
                    if key.startswith("auth:revoked:"):
                        continue
                    if key.startswith("auth:refresh:") or key.startswith("auth:access:") or key.startswith("auth:family_revoked:"):
                        await self.redis_client.delete(key)
            except Exception:
                pass

        if self.session_service is not None and hasattr(self.session_service, "redis_client"):
            try:
                await self.session_service.redis_client.delete(f"active_sessions:{user.id}")
            except Exception:
                pass

        if self.user_repository is not None and hasattr(self.user_repository, "update_user"):
            try:
                async with self.session.begin():
                    user.last_password_change_at = user.last_password_change_at or datetime.now(timezone.utc)
                    await self.user_repository.update_user(user, last_password_change_at=user.last_password_change_at)
            except Exception:
                pass

        await self._log_event(
            "logout_all_sessions_completed",
            user_id=user.id,
            metadata={"reason": reason or "logout_all_sessions", "revoked_count": revoked_count, "preserve_current": preserve_current},
        )
        return {"message": "All sessions revoked successfully.", "revoked_count": revoked_count}

    async def logout_other_devices(self, *, user_id: UUID, current_session_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Revoke all active sessions for the user except the current authenticated session."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not user_id:
            raise ValidationException("User ID is required.")

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        if self.session_service is None:
            raise DatabaseException("Session service is unavailable.")

        try:
            session_payloads = await self.session_service.get_active_sessions(user_id=str(user.id))
        except Exception as exc:
            raise DatabaseException("Unable to retrieve active sessions.") from exc

        if not session_payloads:
            await self._log_event(
                "logout_other_devices_completed",
                user_id=user.id,
                metadata={"reason": reason or "none", "revoked_count": 0, "current_session_id": current_session_id},
            )
            return {"message": "No other sessions to revoke.", "revoked_count": 0}

        revoked_count = 0
        current_session_key = str(current_session_id or "")
        for session_payload in session_payloads:
            session_id = session_payload.get("session_id") or session_payload.get("id")
            if not session_id:
                continue
            if str(session_id) == current_session_key:
                continue

            try:
                if self.token_service is not None and hasattr(self.token_service, "revoke_token"):
                    refresh_token = session_payload.get("refresh_token")
                    if refresh_token:
                        await self.token_service.revoke_token(str(refresh_token), reason=reason or "logout_other_devices")
                    access_token = session_payload.get("access_token")
                    if access_token:
                        await self.token_service.revoke_token(str(access_token), reason=reason or "logout_other_devices")
                else:
                    refresh_token = session_payload.get("refresh_token")
                    if refresh_token:
                        await self._revoke_token(str(refresh_token), token_type="refresh")
                    access_token = session_payload.get("access_token")
                    if access_token:
                        await self._revoke_token(str(access_token), token_type="access")
            except Exception:
                pass

            try:
                family_id = session_payload.get("refresh_token_family_id") or session_payload.get("family_id")
                if family_id and self.token_service is not None and hasattr(self.token_service, "redis_client"):
                    await self._mark_refresh_family_revoked({"family_id": family_id})
            except Exception:
                pass

            try:
                await self.session_service.revoke_session(session_id=str(session_id), reason=reason or "logout_other_devices")
            except Exception:
                pass
            else:
                revoked_count += 1

        if self.device_service is not None:
            try:
                devices = await self.device_service.list_user_devices(user_id=str(user.id))
                for device in devices:
                    device_id = device.get("device_id") or device.get("id")
                    if device_id and device.get("is_active") and str(device_id) != current_session_key:
                        await self.device_service.revoke_device(device_id=str(device_id), reason=reason or "logout_other_devices")
            except Exception:
                pass

        if self.redis_client is not None and hasattr(self.redis_client, "scan_iter"):
            try:
                async for key in self.redis_client.scan_iter(match="auth:*"):
                    if not isinstance(key, str):
                        continue
                    if key.startswith("auth:revoked:"):
                        continue
                    if key.startswith("auth:refresh:") or key.startswith("auth:access:") or key.startswith("auth:family_revoked:"):
                        await self.redis_client.delete(key)
            except Exception:
                pass

        await self._log_event(
            "logout_other_devices_completed",
            user_id=user.id,
            metadata={"reason": reason or "logout_other_devices", "revoked_count": revoked_count, "current_session_id": current_session_key},
        )
        return {"message": "Other sessions revoked successfully.", "revoked_count": revoked_count}

    async def verify_email(self, *, user_id: UUID | None = None, email: str | None = None, otp_code: str) -> dict[str, Any]:
        """Verify a user's email address using the configured OTP workflow."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not otp_code:
            raise ValidationException("OTP code is required.")
        if not user_id and not email:
            raise ValidationException("Email or user ID is required.")

        user = await self._get_user_by_identifier(email=email, user_id=user_id)
        if not user:
            raise ValidationException("User not found.")
        if user.email_verified:
            raise ValidationException("Email is already verified.")

        otp_reference: str | None = None
        otp_record = await self._get_active_otp(user_id=user.id, purpose="email_verification")
        if otp_record is not None:
            otp_reference = otp_record.otp_reference

        try:
            if self.otp_service is not None and otp_reference:
                verified_payload = await self.otp_service.verify_otp(
                    reference_id=otp_reference,
                    otp_code=otp_code,
                    purpose="email_verification",
                    user_id=str(user.id),
                )
            else:
                verified_payload = await self.verify_otp(user_id=user.id, otp_code=otp_code, purpose="email_verification")
        except (OTPException, ValidationException, AuthenticationException):
            raise
        except Exception as exc:
            raise DatabaseException("Email verification failed.") from exc

        if not verified_payload.get("verified"):
            raise OTPException("Email verification failed.")

        try:
            async with self.session.begin():
                user.email_verified = True
                if user.status == "pending":
                    user.status = "active"
                if self.user_repository is not None:
                    await self.user_repository.update_user(user, email_verified=True, status=user.status)
                await self._log_event(
                    "email_verified",
                    user_id=user.id,
                    metadata={"email": user.email},
                )
                if self.notification_service is not None and hasattr(self.notification_service, "send_welcome_notification"):
                    await self.notification_service.send_welcome_notification(user_id=user.id, email=user.email, name=user.first_name or user.username)
                return {"message": "Email verified successfully.", "user": self._serialize_user(user)}
        except Exception as exc:
            raise DatabaseException("Email verification failed.") from exc

    async def resend_verification_otp(self, *, user_id: UUID | None = None, email: str | None = None) -> dict[str, Any]:
        """Send a fresh email-verification OTP while enforcing resend safeguards."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not user_id and not email:
            raise ValidationException("Email or user ID is required.")

        user = await self._get_user_by_identifier(email=email, user_id=user_id)
        if not user:
            raise ValidationException("User not found.")
        if user.email_verified:
            raise ValidationException("Email is already verified.")

        try:
            if self.otp_service is not None:
                result = await self.otp_service.send_otp(
                    recipient=user.email,
                    purpose="email_verification",
                    channel="email",
                    user_id=str(user.id),
                    metadata={"user_id": str(user.id), "email": user.email},
                    delivery_context={"subject": "Verify your email address", "template_name": "otp_email"},
                )
            else:
                otp_code = self.password_utils.generate_otp() if hasattr(self.password_utils, "generate_otp") else generate_otp()
                hashed_value = self._hash_otp(otp_code)
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expiry_minutes)
                otp_record = OTP(
                    user_id=user.id,
                    purpose="email_verification",
                    otp_reference=f"email-{uuid4().hex[:8]}",
                    hashed_value=hashed_value,
                    expires_at=expires_at,
                    is_used=False,
                    is_active=True,
                    attempts=0,
                )
                await self._create_otp_record(otp_record)
                result = {"reference_id": otp_record.otp_reference, "expires_at": expires_at.isoformat()}

            await self._log_event(
                "email_verification_otp_resent",
                user_id=user.id,
                metadata={"email": user.email},
            )
            return {"message": "Verification OTP sent successfully.", "reference_id": result.get("reference_id")}
        except (ResendCooldownActiveException, TooManyResendAttemptsException):
            raise
        except Exception as exc:
            raise DatabaseException("Verification OTP resend failed.") from exc

    async def verify_otp(self, *, user_id: UUID, otp_code: str, purpose: str) -> dict[str, Any]:
        """Validate a one-time password and mark it as used."""
        self._require_session()

        if not otp_code:
            raise ValidationException("OTP code is required.")
        if not purpose:
            raise ValidationException("OTP purpose is required.")

        otp_record = await self._get_active_otp(user_id=user_id, purpose=purpose)
        if not otp_record:
            raise OTPException("OTP is invalid or expired.")

        if otp_record.is_used or not otp_record.is_active:
            raise OTPException("OTP has already been used.")
        if otp_record.expires_at < datetime.now(timezone.utc):
            raise OTPException("OTP has expired.")

        if not self._verify_otp_hash(otp_code, otp_record.hashed_value):
            otp_record.attempts = (otp_record.attempts or 0) + 1
            await self._update_otp_record(otp_record)
            raise OTPException("OTP is invalid.")

        try:
            async with self.session.begin():
                otp_record.is_used = True
                otp_record.is_active = False
                otp_record.verified_at = datetime.now(timezone.utc)
                await self._update_otp_record(otp_record)
                return {"verified": True, "purpose": purpose}
        except Exception as exc:
            raise DatabaseException("OTP verification failed.") from exc

    async def forgot_password(self, *, email: str | None = None, phone: str | None = None) -> dict[str, Any]:
        """Create a secure password-reset token for an existing account without revealing account existence."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not email and not phone:
            raise ValidationException("Email or phone number is required.")

        normalized_email = email.strip().lower() if email else None
        normalized_phone = self._normalize_phone(phone) if phone else None
        user = await self._get_user_by_identifier(email=normalized_email, phone=normalized_phone)

        if user is None:
            await self._log_event("password_reset_requested", metadata={"email": normalized_email or None, "phone": normalized_phone or None})
            return {"message": "If an account exists, a password reset link has been sent."}

        if not user.is_active or user.is_blocked or user.is_suspended:
            await self._log_event("password_reset_requested", user_id=user.id, metadata={"status": user.status})
            return {"message": "If an account exists, a password reset link has been sent."}

        password_service = self.password_service
        if password_service is None:
            password_service = self.password_utils

        try:
            reset_token = await password_service.generate_password_reset_token(user_id=str(user.id), ttl_minutes=getattr(settings, "password_reset_ttl_minutes", None))
        except Exception as exc:
            raise DatabaseException("Password reset request failed.") from exc

        try:
            if self.notification_service is not None and hasattr(self.notification_service, "send_password_reset_email"):
                await self.notification_service.send_password_reset_email(
                    recipients=user.email,
                    reset_token=reset_token,
                    user_name=user.first_name or user.username or user.email,
                )
        except Exception as exc:
            raise DatabaseException("Password reset request failed.") from exc

        try:
            await self._log_event(
                "password_reset_requested",
                user_id=user.id,
                metadata={"email": user.email, "token_generated": True},
            )
        except Exception:
            pass

        return {"message": "If an account exists, a password reset link has been sent."}

    async def reset_password(
        self,
        *,
        otp_code: str | None = None,
        new_password: str,
        user_id: UUID | None = None,
        email: str | None = None,
        reset_token: str | None = None,
        password_confirmation: str | None = None,
    ) -> dict[str, Any]:
        """Reset a password using a single-use password reset token and revoke active security state."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        token = reset_token or otp_code
        if not token:
            raise ValidationException("Reset token is required.")
        if not new_password:
            raise ValidationException("New password is required.")
        if password_confirmation is not None and new_password != password_confirmation:
            raise ValidationException("Password confirmation does not match.")

        password_service = self.password_service or self.password_utils
        user: User | None = None

        try:
            if password_service is not None and hasattr(password_service, "verify_password_reset_token"):
                token_payload = await password_service.verify_password_reset_token(token)
                token_user_id = token_payload.get("user_id")
                if token_user_id:
                    resolved_user = await self._get_user_by_identifier(user_id=UUID(str(token_user_id)))
                    if resolved_user is None:
                        raise AuthenticationException("Invalid reset token.")
                    user = resolved_user
                else:
                    user = await self._get_user_by_identifier(email=email, user_id=user_id)
            else:
                user = await self._get_user_by_identifier(email=email, user_id=user_id)
        except (InvalidResetTokenException, ExpiredResetTokenException) as exc:
            raise AuthenticationException("Invalid or expired password reset token.") from exc
        except (AuthenticationException, ValidationException):
            raise
        except Exception as exc:
            raise DatabaseException("Password reset failed.") from exc

        if user is None:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        try:
            if password_service is not None and hasattr(password_service, "enforce_password_policy"):
                await password_service.enforce_password_policy(
                    password=new_password,
                    confirmation=password_confirmation,
                    user_id=str(user.id),
                )
            elif hasattr(password_service, "validate_password_strength") and not password_service.validate_password_strength(new_password):
                raise ValidationException("Password does not meet the required strength.")
        except (PasswordPolicyViolationException, PasswordReuseViolationException, ValidationException):
            raise
        except Exception as exc:
            raise DatabaseException("Password reset failed.") from exc

        if user.password_hash and self.password_utils.verify_password(new_password, user.password_hash):
            raise ValidationException("New password must be different from the current password.")

        try:
            async with self.session.begin():
                hashed_password = self.password_utils.hash_password(new_password)
                user.password_hash = hashed_password
                user.last_password_change_at = datetime.now(timezone.utc)
                await self.user_repository.update_user(
                    user,
                    password_hash=user.password_hash,
                    last_password_change_at=user.last_password_change_at,
                )

                if hasattr(password_service, "store_password_history"):
                    await password_service.store_password_history(user_id=str(user.id), password_hash=hashed_password)

                if password_service is not None and hasattr(password_service, "invalidate_password_reset_token"):
                    await password_service.invalidate_password_reset_token(token)

                if self.session_service is not None and hasattr(self.session_service, "revoke_all_user_sessions"):
                    try:
                        await self.session_service.revoke_all_user_sessions(user_id=str(user.id), reason="password_reset")
                    except Exception:
                        pass

                if self.redis_client is not None and hasattr(self.redis_client, "scan_iter"):
                    try:
                        async for key in self.redis_client.scan_iter(match="auth:access:*"):
                            if not isinstance(key, str):
                                continue
                            parts = key.split(":")
                            if len(parts) >= 4 and parts[1] == "access" and parts[2] == str(user.id):
                                await self.redis_client.delete(key)
                        async for key in self.redis_client.scan_iter(match="auth:refresh:*"):
                            if not isinstance(key, str):
                                continue
                            parts = key.split(":")
                            if len(parts) >= 4 and parts[1] == "refresh" and parts[2] == str(user.id):
                                await self.redis_client.delete(key)
                    except Exception:
                        pass

                if getattr(settings, "revoke_trusted_devices_on_password_reset", False) and self.device_service is not None:
                    try:
                        devices = await self.device_service.list_user_devices(user_id=str(user.id))
                        for device in devices:
                            device_id = device.get("device_id") or device.get("id")
                            if device_id and device.get("is_trusted"):
                                await self.device_service.revoke_device(device_id=str(device_id), reason="password_reset")
                    except Exception:
                        pass

                await self._log_event(
                    "password_reset_completed",
                    user_id=user.id,
                    metadata={"token_consumed": True, "sessions_revoked": True},
                )

                if self.notification_service is not None:
                    try:
                        if hasattr(self.notification_service, "create_notification"):
                            await self.notification_service.create_notification(
                                user_id=user.id,
                                notification_type="password_changed",
                                title="Password changed",
                                message="Your password was changed successfully.",
                            )
                        elif hasattr(self.notification_service, "send_transactional_email"):
                            await self.notification_service.send_transactional_email(
                                recipients=user.email,
                                template_name="password_changed",
                                user_name=user.first_name or user.username or user.email,
                            )
                        elif hasattr(self.notification_service, "send_email"):
                            await self.notification_service.send_email(
                                recipients=user.email,
                                subject="Password changed",
                                body="Your password was changed successfully.",
                            )
                    except Exception:
                        pass

                return {"message": "Password reset successfully."}
        except (AuthenticationException, ValidationException, OTPException):
            raise
        except Exception as exc:
            raise DatabaseException("Password reset failed.") from exc

    async def change_password(
        self,
        *,
        user_id: UUID,
        current_password: str,
        new_password: str,
        password_confirmation: str | None = None,
    ) -> dict[str, Any]:
        """Change the password for an authenticated user after validating the current password and policy."""
        self._require_session()
        self._require_repository("user_repository", self.user_repository)

        if not current_password:
            raise ValidationException("Current password is required.")
        if not new_password:
            raise ValidationException("New password is required.")
        if password_confirmation is not None and new_password != password_confirmation:
            raise ValidationException("Password confirmation does not match.")

        user = await self.user_repository.get_by_id(user_id)
        if not user or not user.password_hash:
            raise ValidationException("User not found.")
        if not user.is_active or user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")
        if user.account_locked_until and user.account_locked_until > datetime.now(timezone.utc):
            raise AuthenticationException("Account is temporarily locked.")

        password_service = self.password_service or self.password_utils
        if password_service is not None and hasattr(password_service, "verify_password"):
            if not password_service.verify_password(current_password, user.password_hash):
                raise AuthenticationException("Current password is invalid.")
        elif not self.password_utils.verify_password(current_password, user.password_hash):
            raise AuthenticationException("Current password is invalid.")

        if password_service is not None and hasattr(password_service, "verify_password"):
            if password_service.verify_password(new_password, user.password_hash):
                raise ValidationException("New password must be different from the current password.")
        elif self.password_utils.verify_password(new_password, user.password_hash):
            raise ValidationException("New password must be different from the current password.")

        try:
            if password_service is not None and hasattr(password_service, "enforce_password_policy"):
                await password_service.enforce_password_policy(
                    password=new_password,
                    confirmation=password_confirmation,
                    user_id=str(user.id),
                )
            elif hasattr(password_service, "validate_password_strength"):
                if not password_service.validate_password_strength(new_password):
                    raise ValidationException("Password does not meet the required strength.")
            elif not self.password_utils.validate_password_strength(new_password):
                raise ValidationException("Password does not meet the required strength.")
        except (PasswordPolicyViolationException, PasswordReuseViolationException, ValidationException):
            raise
        except Exception as exc:
            raise DatabaseException("Password change failed.") from exc

        try:
            async with self.session.begin():
                hashed_password = self.password_utils.hash_password(new_password)
                user.password_hash = hashed_password
                user.last_password_change_at = datetime.now(timezone.utc)
                await self.user_repository.update_user(
                    user,
                    password_hash=user.password_hash,
                    last_password_change_at=user.last_password_change_at,
                )

                if password_service is not None and hasattr(password_service, "store_password_history"):
                    await password_service.store_password_history(user_id=str(user.id), password_hash=hashed_password)

                if self.session_service is not None and hasattr(self.session_service, "revoke_all_user_sessions"):
                    try:
                        await self.session_service.revoke_all_user_sessions(user_id=str(user.id), reason="password_change")
                    except Exception:
                        pass

                if self.redis_client is not None and hasattr(self.redis_client, "scan_iter"):
                    try:
                        async for key in self.redis_client.scan_iter(match="auth:access:*"):
                            if not isinstance(key, str):
                                continue
                            parts = key.split(":")
                            if len(parts) >= 4 and parts[1] == "access" and parts[2] == str(user.id):
                                await self.redis_client.delete(key)
                        async for key in self.redis_client.scan_iter(match="auth:refresh:*"):
                            if not isinstance(key, str):
                                continue
                            parts = key.split(":")
                            if len(parts) >= 4 and parts[1] == "refresh" and parts[2] == str(user.id):
                                await self.redis_client.delete(key)
                    except Exception:
                        pass

                if getattr(settings, "revoke_trusted_devices_on_password_change", False) and self.device_service is not None:
                    try:
                        devices = await self.device_service.list_user_devices(user_id=str(user.id))
                        for device in devices:
                            device_id = device.get("device_id") or device.get("id")
                            if device_id and device.get("is_trusted"):
                                await self.device_service.revoke_device(device_id=str(device_id), reason="password_change")
                    except Exception:
                        pass

                await self._log_event(
                    "password_changed",
                    user_id=user.id,
                    metadata={"sessions_revoked": True, "tokens_revoked": True},
                )

                if self.notification_service is not None:
                    try:
                        if hasattr(self.notification_service, "create_notification"):
                            await self.notification_service.create_notification(
                                user_id=user.id,
                                notification_type="password_changed",
                                title="Password changed",
                                message="Your password was changed successfully.",
                            )
                        elif hasattr(self.notification_service, "send_transactional_email"):
                            await self.notification_service.send_transactional_email(
                                recipients=user.email,
                                template_name="password_changed",
                                user_name=user.first_name or user.username or user.email,
                            )
                        elif hasattr(self.notification_service, "send_email"):
                            await self.notification_service.send_email(
                                recipients=user.email,
                                subject="Password changed",
                                body="Your password was changed successfully.",
                            )
                    except Exception:
                        pass

                return {"message": "Password changed successfully."}
        except (AuthenticationException, ValidationException, OTPException):
            raise
        except Exception as exc:
            raise DatabaseException("Password change failed.") from exc

    async def change_transaction_pin(self, *, user_id: UUID, new_pin: str, current_pin: str | None = None) -> dict[str, Any]:
        """Update a user's transaction PIN using a hashed reference stored in the wallet."""
        self._require_session()
        self._require_repository("wallet_repository", self.wallet_repository)

        if not new_pin:
            raise ValidationException("New transaction PIN is required.")
        if not self.password_utils.validate_pin(new_pin):
            raise ValidationException("Transaction PIN must be numeric and match the configured length.")

        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if not wallet:
            raise ValidationException("Wallet not found.")
        if wallet.transaction_pin_reference and current_pin is not None:
            if not self.password_utils.verify_pin(current_pin, wallet.transaction_pin_reference):
                raise AuthenticationException("Current transaction PIN is invalid.")

        try:
            async with self.session.begin():
                wallet.transaction_pin_reference = self.password_utils.hash_pin(new_pin)
                await self.wallet_repository.update_wallet(wallet, transaction_pin_reference=wallet.transaction_pin_reference)
                await self._log_event("transaction_pin_changed", user_id=user_id)
                return {"message": "Transaction PIN changed successfully."}
        except Exception as exc:
            raise DatabaseException("Transaction PIN update failed.") from exc

    async def verify_device(
        self,
        *,
        user_id: UUID,
        device_fingerprint: str,
        ip_address: str | None = None,
        device_name: str | None = None,
        device_type: str = "unknown",
    ) -> dict[str, Any]:
        """Create or update a device record and mark it as trusted."""
        self._require_session()

        if not device_fingerprint:
            raise ValidationException("Device fingerprint is required.")

        device = await self._register_or_update_device(
            user_id=user_id,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            device_name=device_name,
            device_type=device_type,
            is_trusted=True,
        )
        return {"message": "Device verified successfully.", "device": self._serialize_device(device)}

    async def validate_token(self, *, token: str, expected_type: str = "access") -> dict[str, Any]:
        """Decode and validate a signed token, including revocation checks."""
        if not token:
            raise ValidationException("Token is required.")
        if await self._is_token_revoked(token):
            raise AuthenticationException("Token has been revoked.")

        try:
            claims = self.jwt_utils.decode_token(token)
        except ValueError as exc:
            raise AuthenticationException("Token is invalid.") from exc

        if claims.get("type") != expected_type:
            raise AuthenticationException("Token type is invalid.")

        user_id = claims.get("sub")
        if not user_id:
            raise AuthenticationException("Token is invalid.")

        user = await self.user_repository.get_by_id(UUID(str(user_id))) if self.user_repository else None
        if not user:
            raise AuthenticationException("User not found.")
        if user.is_blocked or user.is_suspended:
            raise AuthenticationException("Account is not active.")

        return {"valid": True, "user_id": str(user.id), "claims": claims}

    async def revoke_token(self, *, token: str, token_type: str = "access") -> dict[str, Any]:
        """Mark a token as revoked in the configured token store."""
        if not token:
            raise ValidationException("Token is required.")
        await self._revoke_token(token, token_type=token_type)
        return {"message": "Token revoked successfully."}

    async def _decode_token_claims(self, token: str, *, expected_type: str) -> dict[str, Any]:
        if self.token_service is not None and hasattr(self.token_service, "validate_token"):
            return self.token_service.validate_token(token, expected_type=expected_type)
        try:
            return self.jwt_utils.decode_token(token)
        except ValueError as exc:
            raise AuthenticationException("Token is invalid.") from exc

    def _extract_user_id_from_claims(self, claims: dict[str, Any]) -> UUID | None:
        user_id = claims.get("sub") or claims.get("user_id")
        if not user_id:
            return None
        try:
            return UUID(str(user_id))
        except (ValueError, TypeError):
            return None

    async def _mark_refresh_family_revoked(self, claims: dict[str, Any]) -> None:
        family_id = claims.get("family_id") or claims.get("token_family_id")
        if not family_id:
            return
        redis_client = getattr(self.token_service, "redis_client", None) or self.redis_client
        if redis_client is None or not hasattr(redis_client, "setex"):
            return
        ttl_seconds = max(int(getattr(settings, "refresh_token_expire_days", 30) * 24 * 60 * 60), 60)
        await redis_client.setex(f"auth:family_revoked:{family_id}", ttl_seconds, "1")

    async def _handle_failed_login(self, user: User) -> None:
        self._require_repository("user_repository", self.user_repository)
        async with self.session.begin():
            locked_user = await self._get_user_for_update(user.id)
            if locked_user is None:
                return
            failed_attempts = (locked_user.failed_login_attempts or 0) + 1
            locked_user.failed_login_attempts = failed_attempts
            if failed_attempts >= settings.max_login_attempts:
                locked_user.account_locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.account_lock_duration_minutes)
                locked_user.status = "locked"
            await self.user_repository.update_user(
                locked_user,
                failed_login_attempts=locked_user.failed_login_attempts,
                account_locked_until=locked_user.account_locked_until,
                status=locked_user.status,
            )
            await self._log_event("login_failed", user_id=locked_user.id)

    async def _get_user_for_update(self, user_id: UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id).with_for_update())
        return result.scalar_one_or_none()

    async def _register_or_update_device(
        self,
        *,
        user_id: UUID,
        device_fingerprint: str,
        ip_address: str | None = None,
        device_name: str | None = None,
        device_type: str = "unknown",
        is_trusted: bool = False,
    ) -> Device:
        if self.device_repository is not None:
            existing_device = await self.device_repository.get_device_by_identifier(device_fingerprint)
            if existing_device:
                existing_device.user_id = user_id
                existing_device.ip_address = ip_address
                existing_device.device_name = device_name
                existing_device.device_type = device_type
                existing_device.is_trusted = is_trusted
                existing_device.last_seen_at = datetime.now(timezone.utc)
                return await self.device_repository.update_device(existing_device)

            device = Device(
                user_id=user_id,
                device_fingerprint=device_fingerprint,
                ip_address=ip_address,
                device_name=device_name,
                device_type=device_type,
                is_trusted=is_trusted,
                is_active=True,
                is_revoked=False,
            )
            return await self.device_repository.create_device(device)

        device = Device(
            user_id=user_id,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            device_name=device_name,
            device_type=device_type,
            is_trusted=is_trusted,
            is_active=True,
            is_revoked=False,
        )
        self.session.add(device)
        await self.session.flush()
        await self.session.refresh(device)
        return device

    async def _get_user_by_identifier(self, *, email: str | None = None, phone: str | None = None, user_id: UUID | None = None) -> User | None:
        self._require_repository("user_repository", self.user_repository)
        if user_id:
            return await self.user_repository.get_by_id(user_id)
        if email:
            return await self.user_repository.get_by_email(email.strip().lower())
        if phone:
            return await self.user_repository.get_by_phone(phone)
        return None

    async def _username_exists(self, username: str) -> bool:
        self._require_repository("user_repository", self.user_repository)
        if hasattr(self.user_repository, "get_by_username"):
            return await self.user_repository.get_by_username(username) is not None
        result = await self.session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none() is not None

    async def _create_default_preferences(self, user_id: UUID) -> None:
        if self.notification_service is None:
            return
        if hasattr(self.notification_service, "create_default_preferences"):
            await self.notification_service.create_default_preferences(user_id=user_id)

    async def _get_active_otp(self, *, user_id: UUID, purpose: str) -> OTP | None:
        if self.otp_repository is not None and hasattr(self.otp_repository, "get_active_otp"):
            return await self.otp_repository.get_active_otp(user_id=user_id, purpose=purpose)

        result = await self.session.execute(
            select(OTP)
            .where(OTP.user_id == user_id, OTP.purpose == purpose, OTP.is_active.is_(True), OTP.is_used.is_(False))
            .order_by(OTP.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def _create_otp_record(self, otp: OTP) -> OTP:
        if self.otp_repository is not None and hasattr(self.otp_repository, "create_otp"):
            return await self.otp_repository.create_otp(otp)
        self.session.add(otp)
        await self.session.flush()
        await self.session.refresh(otp)
        return otp

    async def _update_otp_record(self, otp: OTP) -> OTP:
        if self.otp_repository is not None and hasattr(self.otp_repository, "update_otp"):
            return await self.otp_repository.update_otp(otp)
        self.session.add(otp)
        await self.session.flush()
        await self.session.refresh(otp)
        return otp

    async def _cache_token(self, token: str, *, token_type: str, user_id: UUID) -> None:
        if self.redis_client is None:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        ttl_seconds = self._token_ttl_seconds(token_type)
        if hasattr(self.redis_client, "setex"):
            await self.redis_client.setex(f"auth:{token_type}:{user_id}:{token_hash}", ttl_seconds, "1")

    async def _is_token_revoked(self, token: str) -> bool:
        if self.redis_client is None:
            return False
        jti = self._extract_token_jti(token)
        if not jti:
            return False
        if hasattr(self.redis_client, "exists"):
            return bool(await self.redis_client.exists(f"{TokenService.REVOCATION_PREFIX}:{jti}"))
        return False

    async def _has_active_refresh_token(self, token: str, user_id: str | None) -> bool:
        if self.redis_client is None:
            return True
        if not user_id:
            return False
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if hasattr(self.redis_client, "get"):
            value = await self.redis_client.get(f"auth:refresh:{UUID(str(user_id))}:{token_hash}")
            return bool(value)
        return False

    async def _revoke_token(self, token: str, *, token_type: str) -> None:
        if self.redis_client is None:
            return
        claims = self._extract_token_claims(token)
        jti = str(claims.get("jti") or "")
        if hasattr(self.redis_client, "setex"):
            if jti:
                ttl_seconds = self._token_ttl_seconds(token_type)
                exp = claims.get("exp")
                if exp is not None:
                    try:
                        exp_dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
                        ttl_seconds = max(int((exp_dt - datetime.now(timezone.utc)).total_seconds()), 60)
                    except (TypeError, ValueError):
                        ttl_seconds = self._token_ttl_seconds(token_type)
                await self.redis_client.setex(f"{TokenService.REVOCATION_PREFIX}:{jti}", ttl_seconds, "1")
        if hasattr(self.redis_client, "delete"):
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            await self.redis_client.delete(f"auth:{token_type}:{token_hash}")

    def _extract_token_claims(self, token: str) -> dict[str, Any]:
        if self.token_service is not None and hasattr(self.token_service, "extract_claims"):
            claims = self.token_service.extract_claims(token)
            if isinstance(claims, dict):
                return claims
        try:
            return self.jwt_utils.decode_token(token)
        except Exception:
            return {}

    def _extract_token_jti(self, token: str) -> str | None:
        claims = self._extract_token_claims(token)
        jti = claims.get("jti")
        if not jti:
            return None
        return str(jti)

    def _issue_access_token(self, user: User) -> str:
        return self.jwt_utils.create_access_token(
            str(user.id),
            extra_claims={
                "email": user.email,
                "user_id": str(user.id),
                "email_verified": user.email_verified,
                "role": getattr(user, "role", None) or "user",
                "jti": uuid4().hex,
            },
        )

    def _issue_refresh_token(self, user: User) -> str:
        return self.jwt_utils.create_refresh_token(
            str(user.id),
            extra_claims={
                "email": user.email,
                "user_id": str(user.id),
                "jti": uuid4().hex,
            },
        )

    def _hash_otp(self, otp_code: str) -> str:
        return hashlib.sha256(otp_code.encode("utf-8")).hexdigest()

    def _verify_otp_hash(self, otp_code: str, hashed_value: str) -> bool:
        return self._hash_otp(otp_code) == hashed_value

    def _token_ttl_seconds(self, token_type: str) -> int:
        if token_type == "refresh":
            return settings.refresh_token_expire_days * 24 * 60 * 60
        return settings.access_token_expire_minutes * 60

    def _normalize_phone(self, phone: str | None) -> str | None:
        if not phone:
            return None
        normalized = self.password_utils.normalize_phone_number(phone)
        return normalized or None

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "auth_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        audit_hook = self.audit_hook
        if audit_hook is not None:
            try:
                await audit_hook(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                audit_hook(event_name, user_id=user_id, metadata=metadata)

    def _serialize_user(self, user: User) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "username": user.username,
            "status": user.status,
            "is_active": user.is_active,
            "email_verified": user.email_verified,
            "phone_verified": user.phone_verified,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }

    def _serialize_device(self, device: Device) -> dict[str, Any]:
        return {
            "id": str(device.id),
            "device_fingerprint": device.device_fingerprint,
            "device_name": device.device_name,
            "device_type": device.device_type,
            "is_trusted": device.is_trusted,
            "is_active": device.is_active,
            "ip_address": device.ip_address,
        }

    async def _serialize_session(self, session_payload: dict[str, Any], *, current_session_id: str | None = None) -> dict[str, Any]:
        device_id = session_payload.get("device_id")
        trusted_device = False
        if device_id and self.device_service is not None:
            try:
                device_payload = await self.device_service.retrieve_device(device_id=str(device_id))
                trusted_device = bool(device_payload.get("is_trusted", False))
            except Exception:
                trusted_device = False

        session_id = session_payload.get("session_id") or session_payload.get("id")
        return {
            "session_id": str(session_id) if session_id is not None else None,
            "device_name": session_payload.get("device_name"),
            "device_type": session_payload.get("device_type"),
            "browser": session_payload.get("browser"),
            "operating_system": session_payload.get("operating_system"),
            "ip_address": session_payload.get("ip_address"),
            "country": session_payload.get("country"),
            "city": session_payload.get("city"),
            "login_time": session_payload.get("created_at"),
            "last_activity": session_payload.get("last_activity_at"),
            "current_session": bool(current_session_id and str(session_id) == str(current_session_id)),
            "trusted_device": trusted_device,
            "status": session_payload.get("status") or "active",
        }

    def _require_repository(self, name: str, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError(f"{name} is required for AuthService.")

    def _require_session(self) -> None:
        if self.session is None:
            raise RuntimeError("Async session is required for AuthService.")
