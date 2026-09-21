from __future__ import annotations

"""User route registration for the CosmozPay backend."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.user_controller import (
    AccountInfoRequest,
    ProfileImageUploadRequest,
    ProfileUpdateRequest,
    UserController,
)
from app.repositories.bank_account_repository import BankAccountRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.kyc_repository import KYCRepository
from app.repositories.user_repository import UserRepository
from app.services.user.bank_account import BankAccountService
from app.services.user.device import DeviceService
from app.services.user.kyc import KYCService
from app.services.user.profile import ProfileService
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


async def get_user_service(session: AsyncSession = Depends(get_db)) -> UserService:
    """Compose the user service graph per request using the active database session."""
    user_repository = UserRepository(session=session)
    kyc_repository = KYCRepository(session=session)
    bank_account_repository = BankAccountRepository(session=session)
    device_repository = DeviceRepository(session=session)

    profile_service = ProfileService(user_repository=user_repository, session=session)
    kyc_service = KYCService(
        user_repository=user_repository,
        kyc_repository=kyc_repository,
        session=session,
    )
    bank_account_service = BankAccountService(
        user_repository=user_repository,
        bank_account_repository=bank_account_repository,
        session=session,
    )
    device_service = DeviceService(
        user_repository=user_repository,
        device_repository=device_repository,
        session=session,
    )

    return UserService(
        profile_service=profile_service,
        kyc_service=kyc_service,
        bank_account_service=bank_account_service,
        device_service=device_service,
    )


async def get_user_controller(user_service: UserService = Depends(get_user_service)) -> UserController:
    """Instantiate the user controller with a request-scoped user service."""
    return UserController(user_service)


@router.get("/me", status_code=status.HTTP_200_OK)
async def get_profile(request: Request, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.get_profile(request=request)


@router.put("/me", status_code=status.HTTP_200_OK)
async def update_profile(request: Request, payload: ProfileUpdateRequest, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.update_profile(payload, request=request)


@router.get("/me/account", status_code=status.HTTP_200_OK)
async def get_account_info(request: Request, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.get_account_info(request=request)


@router.post("/me/profile-image", status_code=status.HTTP_201_CREATED)
async def upload_profile_image(
    request: Request,
    file: UploadFile = File(...),
    public_id: str | None = None,
    controller: UserController = Depends(get_user_controller),
) -> dict[str, Any]:
    return await controller.upload_profile_image(request=request, file=file, public_id=public_id)


@router.delete("/me/profile-image", status_code=status.HTTP_200_OK)
async def remove_profile_image(request: Request, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.remove_profile_image(request=request)


@router.get("/me/devices", status_code=status.HTTP_200_OK)
async def list_devices(request: Request, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.list_devices(request=request)


@router.get("/me/verification-status", status_code=status.HTTP_200_OK)
async def get_verification_status(request: Request, controller: UserController = Depends(get_user_controller)) -> dict[str, Any]:
    return await controller.get_verification_status(request=request)
