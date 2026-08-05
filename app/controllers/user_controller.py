from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.schemas.user_schema import UserUpdate
from app.services.user_service import UserService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class ProfileUpdateRequest(UserUpdate):
    """Request schema for profile updates."""


class ProfileImageUploadRequest(BaseModel):
    """Request payload wrapper for profile image uploads."""

    public_id: str | None = Field(default=None, description="Optional Cloudinary public identifier.")


class AccountInfoRequest(BaseModel):
    """Request schema for account info retrieval."""

    user_id: UUID = Field(..., description="Identifier of the user whose account info is requested.")


class UserController:
    """Thin FastAPI controller for user-management endpoints."""

    def __init__(self, user_service: UserService, logger: logging.Logger | None = None) -> None:
        self.user_service = user_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/users", tags=["Users"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.get("/me", status_code=status.HTTP_200_OK)(self.get_profile)
        self.router.put("/me", status_code=status.HTTP_200_OK)(self.update_profile)
        self.router.get("/me/account", status_code=status.HTTP_200_OK)(self.get_account_info)
        self.router.post("/me/profile-image", status_code=status.HTTP_201_CREATED)(self.upload_profile_image)
        self.router.delete("/me/profile-image", status_code=status.HTTP_200_OK)(self.remove_profile_image)
        self.router.get("/me/devices", status_code=status.HTTP_200_OK)(self.list_devices)
        self.router.get("/me/verification-status", status_code=status.HTTP_200_OK)(self.get_verification_status)

    async def get_profile(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle profile retrieval requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="get_profile",
            handler=self.user_service.get_profile,
            payload={"user_id": target_user_id},
            success_message="Profile retrieved successfully.",
        )

    async def update_profile(self, payload: ProfileUpdateRequest, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle profile update requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="update_profile",
            handler=self.user_service.update_profile,
            payload={"user_id": target_user_id, "profile_data": payload.model_dump(exclude_unset=True)},
            success_message="Profile updated successfully.",
        )

    async def get_account_info(self, request: AccountInfoRequest) -> dict[str, Any]:
        """Handle account information requests."""
        return await self._execute(
            action="get_account_info",
            handler=self.user_service.get_profile,
            payload={"user_id": request.user_id},
            success_message="Account information retrieved successfully.",
        )

    async def upload_profile_image(
        self,
        file: UploadFile = File(...),
        public_id: str | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Handle profile image upload requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="upload_profile_image",
            handler=self.user_service.upload_profile_photo,
            payload={"user_id": target_user_id, "file_path": file.filename or "", "public_id": public_id},
            success_message="Profile image uploaded successfully.",
        )

    async def remove_profile_image(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle profile image removal requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="remove_profile_image",
            handler=self.user_service.remove_profile_photo,
            payload={"user_id": target_user_id},
            success_message="Profile image removed successfully.",
        )

    async def list_devices(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle user device listing requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="list_devices",
            handler=self.user_service.list_devices,
            payload={"user_id": target_user_id},
            success_message="Devices retrieved successfully.",
        )

    async def get_verification_status(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle account verification status requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="get_verification_status",
            handler=self.user_service.get_kyc_status,
            payload={"user_id": target_user_id},
            success_message="Verification status retrieved successfully.",
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

        log_api_event(self.logger, "user_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "user_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )

    def _resolve_user_id(self) -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
