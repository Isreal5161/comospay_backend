from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.schemas.user_schema import UserCreate, UserLoginSchema
from app.services.auth_service import AuthService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class RegisterRequest(UserCreate):
    """Request schema for user registration."""

    device_fingerprint: str | None = Field(default=None, description="Optional device fingerprint.")
    ip_address: str | None = Field(default=None, description="Optional client IP address.")
    device_name: str | None = Field(default=None, description="Optional device name.")
    device_type: str | None = Field(default="unknown", description="Optional device type.")


class LoginRequest(UserLoginSchema):
    """Request schema for user login."""

    device_fingerprint: str | None = Field(default=None, description="Optional device fingerprint.")
    ip_address: str | None = Field(default=None, description="Optional client IP address.")
    device_name: str | None = Field(default=None, description="Optional device name.")
    device_type: str | None = Field(default="unknown", description="Optional device type.")


class TokenRefreshRequest(BaseModel):
    """Request schema for refresh token requests."""

    refresh_token: str = Field(..., description="Refresh token used to obtain a new access token.")


class LogoutRequest(BaseModel):
    """Request schema for logout requests."""

    access_token: str | None = Field(default=None, description="Access token to revoke.")
    refresh_token: str | None = Field(default=None, description="Refresh token to revoke.")


class EmailVerificationRequest(BaseModel):
    """Request schema for email verification requests."""

    user_id: UUID | None = Field(default=None, description="Optional user identifier.")
    email: EmailStr | None = Field(default=None, description="Optional email address.")
    otp_code: str = Field(..., description="OTP code for email verification.")


class OTPVerificationRequest(BaseModel):
    """Request schema for verifying one-time passwords."""

    user_id: UUID | None = Field(default=None, description="Optional user identifier.")
    email: EmailStr | None = Field(default=None, description="Optional email address.")
    otp_code: str = Field(..., description="OTP code supplied by the user.")
    purpose: str = Field(..., description="OTP purpose, such as password_reset or email_verification.")


class ResendOTPRequest(BaseModel):
    """Request schema for OTP resend requests."""

    email: EmailStr | None = Field(default=None, description="Email address to resend OTP to.")
    phone: str | None = Field(default=None, description="Phone number to resend OTP to.")


class PasswordResetRequest(BaseModel):
    """Request schema for password reset completion."""

    otp_code: str = Field(..., description="Password reset OTP code.")
    new_password: str = Field(..., min_length=8, description="New password for the account.")
    user_id: UUID | None = Field(default=None, description="Optional user identifier.")
    email: EmailStr | None = Field(default=None, description="Optional email address.")


class ChangePasswordRequest(BaseModel):
    """Request schema for authenticated password changes."""

    user_id: UUID = Field(..., description="Identifier of the user changing password.")
    current_password: str = Field(..., min_length=8, description="Current password.")
    new_password: str = Field(..., min_length=8, description="New password.")


class DeviceVerificationRequest(BaseModel):
    """Request schema for device verification."""

    user_id: UUID = Field(..., description="Identifier of the user verifying the device.")
    device_fingerprint: str = Field(..., description="Device fingerprint used for verification.")
    ip_address: str | None = Field(default=None, description="Optional client IP address.")
    device_name: str | None = Field(default=None, description="Optional device name.")
    device_type: str | None = Field(default="unknown", description="Device type.")


class AuthController:
    """Thin FastAPI controller for authentication endpoints."""

    def __init__(self, auth_service: AuthService, logger: logging.Logger | None = None) -> None:
        self.auth_service = auth_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/auth", tags=["Authentication"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/register", status_code=status.HTTP_201_CREATED)(self.register)
        self.router.post("/login", status_code=status.HTTP_200_OK)(self.login)
        self.router.post("/refresh", status_code=status.HTTP_200_OK)(self.refresh_token)
        self.router.post("/logout", status_code=status.HTTP_200_OK)(self.logout)
        self.router.post("/verify-email", status_code=status.HTTP_200_OK)(self.verify_email)
        self.router.post("/verify-otp", status_code=status.HTTP_200_OK)(self.verify_otp)
        self.router.post("/resend-otp", status_code=status.HTTP_200_OK)(self.resend_otp)
        self.router.post("/forgot-password", status_code=status.HTTP_200_OK)(self.forgot_password)
        self.router.post("/reset-password", status_code=status.HTTP_200_OK)(self.reset_password)
        self.router.post("/change-password", status_code=status.HTTP_200_OK)(self.change_password)
        self.router.post("/verify-device", status_code=status.HTTP_200_OK)(self.verify_device)

    async def register(self, payload: RegisterRequest) -> dict[str, Any]:
        """Handle user registration requests."""
        result = await self._execute(
            action="register",
            handler=self.auth_service.register_user,
            payload={
                "email": payload.email,
                "password": payload.password,
                "first_name": payload.first_name,
                "last_name": payload.last_name,
                "phone": payload.phone,
                "username": payload.username,
                "device_fingerprint": payload.device_fingerprint,
                "ip_address": payload.ip_address,
                "device_name": payload.device_name,
                "device_type": payload.device_type or "unknown",
            },
            success_message="Registration successful.",
        )
        return result

    async def login(self, payload: LoginRequest) -> dict[str, Any]:
        """Handle user login requests."""
        identifier = payload.identifier.strip()
        email = identifier if "@" in identifier else None
        phone = None if email else identifier

        result = await self._execute(
            action="login",
            handler=self.auth_service.login,
            payload={
                "email": email,
                "phone": phone,
                "password": payload.password,
                "device_fingerprint": payload.device_fingerprint,
                "ip_address": payload.ip_address,
                "device_name": payload.device_name,
                "device_type": payload.device_type or "unknown",
            },
            success_message="Login successful.",
        )
        return result

    async def refresh_token(self, payload: TokenRefreshRequest) -> dict[str, Any]:
        """Handle refresh token requests."""
        return await self._execute(
            action="refresh_token",
            handler=self.auth_service.refresh_token,
            payload={"refresh_token": payload.refresh_token},
            success_message="Token refreshed successfully.",
        )

    async def logout(self, payload: LogoutRequest) -> dict[str, Any]:
        """Handle logout requests."""
        return await self._execute(
            action="logout",
            handler=self.auth_service.logout,
            payload={"access_token": payload.access_token, "refresh_token": payload.refresh_token},
            success_message="Logged out successfully.",
        )

    async def verify_email(self, payload: EmailVerificationRequest) -> dict[str, Any]:
        """Handle OTP email verification requests."""
        return await self._execute(
            action="verify_email",
            handler=self.auth_service.verify_email,
            payload={"user_id": payload.user_id, "email": payload.email, "otp_code": payload.otp_code},
            success_message="Email verified successfully.",
        )

    async def verify_otp(self, payload: OTPVerificationRequest) -> dict[str, Any]:
        """Handle generic OTP verification requests."""
        return await self._execute(
            action="verify_otp",
            handler=self.auth_service.verify_otp,
            payload={"user_id": payload.user_id, "otp_code": payload.otp_code, "purpose": payload.purpose},
            success_message="OTP verified successfully.",
        )

    async def resend_otp(self, payload: ResendOTPRequest) -> dict[str, Any]:
        """Handle OTP resend requests by delegating to forgot password flow."""
        return await self._execute(
            action="resend_otp",
            handler=self.auth_service.forgot_password,
            payload={"email": payload.email, "phone": payload.phone},
            success_message="OTP resend requested successfully.",
        )

    async def forgot_password(self, payload: ResendOTPRequest) -> dict[str, Any]:
        """Handle forgot password requests."""
        return await self._execute(
            action="forgot_password",
            handler=self.auth_service.forgot_password,
            payload={"email": payload.email, "phone": payload.phone},
            success_message="Password reset request received.",
        )

    async def reset_password(self, payload: PasswordResetRequest) -> dict[str, Any]:
        """Handle password reset requests."""
        return await self._execute(
            action="reset_password",
            handler=self.auth_service.reset_password,
            payload={
                "otp_code": payload.otp_code,
                "new_password": payload.new_password,
                "user_id": payload.user_id,
                "email": payload.email,
            },
            success_message="Password reset successfully.",
        )

    async def change_password(self, payload: ChangePasswordRequest) -> dict[str, Any]:
        """Handle change password requests."""
        return await self._execute(
            action="change_password",
            handler=self.auth_service.change_password,
            payload={
                "user_id": payload.user_id,
                "current_password": payload.current_password,
                "new_password": payload.new_password,
            },
            success_message="Password changed successfully.",
        )

    async def verify_device(self, payload: DeviceVerificationRequest) -> dict[str, Any]:
        """Handle device verification requests."""
        return await self._execute(
            action="verify_device",
            handler=self.auth_service.verify_device,
            payload={
                "user_id": payload.user_id,
                "device_fingerprint": payload.device_fingerprint,
                "ip_address": payload.ip_address,
                "device_name": payload.device_name,
                "device_type": payload.device_type or "unknown",
            },
            success_message="Device verified successfully.",
        )

    async def _execute(
        self,
        action: str,
        handler: Callable[..., Awaitable[dict[str, Any]]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            result = await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

        log_api_event(self.logger, "auth_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "auth_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )
