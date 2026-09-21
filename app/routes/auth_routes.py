from __future__ import annotations

"""Authentication route registration for the CosmozPay backend."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.auth_controller import (
    AuthController,
    ChangePasswordRequest,
    DeviceVerificationRequest,
    EmailVerificationRequest,
    LoginRequest,
    LogoutRequest,
    OTPVerificationRequest,
    PasswordResetRequest,
    RegisterRequest,
    ResendOTPRequest,
    TokenRefreshRequest,
)
from app.repositories.device_repository import DeviceRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.auth.device_service import DeviceService
from app.services.auth.otp_service import OTPService
from app.services.auth.password_service import PasswordService
from app.services.auth.session_service import SessionService
from app.services.auth.token_service import TokenService
from app.services.auth_service import AuthService
from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
)
from app.services.wallet_service import WalletService

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    """Compose the auth service graph per request using the active database session."""
    user_repository = UserRepository(session=session)
    wallet_repository = WalletRepository(session=session)
    device_repository = DeviceRepository(session=session)

    password_service = PasswordService()
    otp_service = OTPService(redis_client=None, notification_service=None)
    token_service = TokenService()
    session_service = SessionService()
    # auth/device service uses an auth-specific Redis-backed device service
    device_service = DeviceService(redis_client=None)

    # Compose a minimal request-scoped wallet facade from concrete internal services
    transaction_repository = TransactionRepository(session=session)
    from app.repositories.virtual_account_repository import VirtualAccountRepository
    from app.services.notification_service import build_notification_service
    from app.services.virtual_account_service import VirtualAccountService
    from app.integrations.payments.flutterwave.client import FlutterwaveClient
    from app.integrations.payments.flutterwave.virtual_accounts import FlutterwaveVirtualAccountService

    virtual_account_repository = VirtualAccountRepository(session=session)
    flutterwave_client = FlutterwaveClient()
    flutterwave_va = FlutterwaveVirtualAccountService(client=flutterwave_client)
    provider_map = {"flutterwave": flutterwave_va}

    virtual_account_service = VirtualAccountService(
        virtual_account_repository=virtual_account_repository,
        wallet_repository=wallet_repository,
        user_repository=user_repository,
        provider_services=provider_map,
        notification_service=build_notification_service(session=session, redis_client=None),
        session=session,
    )

    wallet_manager = WalletManager(user_repository=user_repository, wallet_repository=wallet_repository, virtual_account_service=virtual_account_service, session=session)
    funding_service = WalletFundingService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=None,
        payment_service=None,
        user_repository=user_repository,
        session=session,
    )
    transfer_service = WalletTransferService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        session=session,
    )
    pin_service = WalletPinService(user_repository=user_repository, wallet_repository=wallet_repository, session=session)
    statement_service = WalletStatementService(wallet_repository=wallet_repository, transaction_repository=transaction_repository, session=session)

    wallet_service = WalletService(
        wallet_manager=wallet_manager,
        funding_service=funding_service,
        transfer_service=transfer_service,
        pin_service=pin_service,
        statement_service=statement_service,
    )

    return AuthService(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        otp_repository=None,
        device_repository=device_repository,
        session=session,
        password_service=password_service,
        otp_service=otp_service,
        token_service=token_service,
        session_service=session_service,
        device_service=device_service,
        wallet_service=wallet_service,
        notification_service=build_notification_service(session=session, redis_client=None),
    )


async def get_auth_controller(auth_service: AuthService = Depends(get_auth_service)) -> AuthController:
    """Instantiate the auth controller with a request-scoped auth service."""
    return AuthController(auth_service)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.register(payload)


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.login(payload)


@router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh_token(payload: TokenRefreshRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.refresh_token(payload)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(payload: LogoutRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.logout(payload)


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(payload: EmailVerificationRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.verify_email(payload)


@router.post("/verify-otp", status_code=status.HTTP_200_OK)
async def verify_otp(payload: OTPVerificationRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.verify_otp(payload)


@router.post("/resend-otp", status_code=status.HTTP_200_OK)
async def resend_otp(payload: ResendOTPRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.resend_otp(payload)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(payload: ResendOTPRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.forgot_password(payload)


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(payload: PasswordResetRequest, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.reset_password(payload)


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(payload: ChangePasswordRequest, request: Request, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.change_password(payload, request=request)


@router.post("/verify-device", status_code=status.HTTP_200_OK)
async def verify_device(payload: DeviceVerificationRequest, request: Request, controller: AuthController = Depends(get_auth_controller)) -> dict[str, Any]:
    return await controller.verify_device(payload, request=request)
