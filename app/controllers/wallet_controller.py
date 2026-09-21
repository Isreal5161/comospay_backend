from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.wallet_service import WalletService
from app.utils.exceptions import AppException, AuthorizationException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class WalletInfoRequest(BaseModel):
    """Request schema for wallet information lookups."""

    wallet_id: UUID = Field(..., description="Identifier of the wallet to inspect.")


class WalletBalanceRequest(BaseModel):
    """Request schema for wallet balance lookups."""

    wallet_id: UUID = Field(..., description="Identifier of the wallet to inspect.")


class WalletStatementRequest(BaseModel):
    """Request schema for wallet statement retrieval."""

    wallet_id: UUID = Field(..., description="Identifier of the wallet whose statements are requested.")
    user_id: UUID = Field(..., description="Identifier of the owning user.")
    start_date: str | None = Field(default=None, description="Optional statement start date.")
    end_date: str | None = Field(default=None, description="Optional statement end date.")
    status: str | None = Field(default=None, description="Optional transaction status filter.")
    transaction_type: str | None = Field(default=None, description="Optional transaction type filter.")
    page: int = Field(default=1, ge=1, description="Result page number.")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page.")


class WalletFundingRequest(BaseModel):
    """Request schema for wallet funding requests."""

    user_id: UUID = Field(..., description="Identifier of the funding user.")
    wallet_id: UUID = Field(..., description="Identifier of the target wallet.")
    amount: Decimal = Field(..., gt=0, description="Funding amount.")
    provider_name: str = Field(default="flutterwave", description="Funding provider name.")
    provider_reference: str | None = Field(default=None, description="Optional provider reference.")
    currency: str = Field(default="NGN", description="Funding currency.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class WalletTransferRequest(BaseModel):
    """Request schema for wallet transfer requests."""

    sender_user_id: UUID = Field(..., description="Identifier of the sender user.")
    sender_wallet_id: UUID = Field(..., description="Identifier of the sender wallet.")
    recipient_user_id: UUID = Field(..., description="Identifier of the recipient user.")
    recipient_wallet_id: UUID | None = Field(default=None, description="Optional recipient wallet identifier.")
    amount: Decimal = Field(..., gt=0, description="Transfer amount.")
    transaction_pin: str | None = Field(default=None, description="Optional transaction PIN.")
    description: str | None = Field(default=None, description="Optional transfer description.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class TransactionPinRequest(BaseModel):
    """Request schema for transfer PIN creation and update."""

    user_id: UUID = Field(..., description="Identifier of the user managing the PIN.")
    pin: str = Field(..., min_length=4, description="Transfer PIN.")
    confirm_pin: str = Field(..., min_length=4, description="Repeated transfer PIN for confirmation.")
    require_complexity: bool = Field(default=False, description="Whether complexity rules should be enforced.")


class TransactionPinVerificationRequest(BaseModel):
    """Request schema for transfer PIN verification."""

    user_id: UUID = Field(..., description="Identifier of the user whose PIN is being verified.")
    pin: str = Field(..., min_length=4, description="Transfer PIN to verify.")


class TransactionPinResetRequest(BaseModel):
    """Request schema for transfer PIN reset."""

    user_id: UUID = Field(..., description="Identifier of the user resetting the PIN.")
    new_pin: str = Field(..., min_length=4, description="New transfer PIN.")
    confirm_pin: str = Field(..., min_length=4, description="Repeated new transfer PIN.")
    require_complexity: bool = Field(default=False, description="Whether complexity rules should be enforced.")


class TransactionHistoryRequest(BaseModel):
    """Request schema for transaction history retrieval."""

    user_id: UUID = Field(..., description="Identifier of the owning user.")
    wallet_id: UUID = Field(..., description="Identifier of the wallet.")
    page: int = Field(default=1, ge=1, description="Result page number.")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of results per page.")


class TransactionDetailRequest(BaseModel):
    """Request schema for transaction detail retrieval."""

    user_id: UUID = Field(..., description="Identifier of the owning user.")
    transaction_id: UUID = Field(..., description="Identifier of the transaction.")


class WalletController:
    """Thin FastAPI controller for wallet endpoints."""

    def __init__(self, wallet_service: WalletService, logger: logging.Logger | None = None) -> None:
        self.wallet_service = wallet_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/wallets", tags=["Wallets"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.get("", status_code=status.HTTP_200_OK)(self.get_wallet)
        self.router.get("/balance", status_code=status.HTTP_200_OK)(self.get_wallet_balance)
        self.router.get("/statement", status_code=status.HTTP_200_OK)(self.get_wallet_statement)
        self.router.post("/fund", status_code=status.HTTP_200_OK)(self.fund_wallet)
        self.router.post("/transfer", status_code=status.HTTP_200_OK)(self.transfer)
        self.router.post("/pin", status_code=status.HTTP_201_CREATED)(self.create_transaction_pin)
        self.router.put("/pin", status_code=status.HTTP_200_OK)(self.update_transaction_pin)
        self.router.post("/pin/verify", status_code=status.HTTP_200_OK)(self.verify_transaction_pin)
        self.router.post("/pin/reset", status_code=status.HTTP_200_OK)(self.reset_transaction_pin)
        self.router.get("/transactions", status_code=status.HTTP_200_OK)(self.get_transaction_history)
        self.router.get("/transactions/{transaction_id}", status_code=status.HTTP_200_OK)(self.get_transaction_details)

    async def get_wallet(self, payload: WalletInfoRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet information retrieval requests."""
        # IDOR FIX: Validate authenticated user owns the wallet being accessed
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        # Query wallet to verify ownership before proceeding
        wallet = await self.wallet_service.get_wallet(wallet_id=payload.wallet_id)
        if wallet and wallet.get("user_id") and UUID(str(wallet["user_id"])) != authenticated_user_id:
            raise AuthorizationException("You do not have access to this wallet.")
        
        return await self._execute(
            action="get_wallet",
            handler=self.wallet_service.get_wallet,
            payload={"wallet_id": payload.wallet_id},
            success_message="Wallet retrieved successfully.",
        )

    async def get_wallet_balance(self, payload: WalletBalanceRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet balance retrieval requests."""
        # IDOR FIX: Validate authenticated user owns the wallet being accessed
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        # Query wallet to verify ownership before proceeding
        wallet = await self.wallet_service.get_wallet(wallet_id=payload.wallet_id)
        if wallet and wallet.get("user_id") and UUID(str(wallet["user_id"])) != authenticated_user_id:
            raise AuthorizationException("You do not have access to this wallet.")
        
        return await self._execute(
            action="get_wallet_balance",
            handler=self.wallet_service.get_wallet_balance,
            payload={"wallet_id": payload.wallet_id},
            success_message="Wallet balance retrieved successfully.",
        )

    async def get_wallet_statement(self, payload: WalletStatementRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet statement retrieval requests."""
        # IDOR FIX: Validate authenticated user matches the user in the request
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot retrieve statement for another user's wallet.")

        return await self._execute(
            action="get_wallet_statement",
            handler=self.wallet_service.get_wallet_statement,
            payload={
                "user_id": payload.user_id,
                "wallet_id": payload.wallet_id,
                "start_date": payload.start_date,
                "end_date": payload.end_date,
                "status": payload.status,
                "transaction_type": payload.transaction_type,
                "page": payload.page,
                "page_size": payload.page_size,
            },
            success_message="Wallet statement retrieved successfully.",
        )

    async def fund_wallet(self, payload: WalletFundingRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet funding requests."""
        # IDOR FIX: Validate authenticated user matches the user requesting the funding
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot fund wallet for another user.")

        return await self._execute(
            action="fund_wallet",
            handler=self.wallet_service.initialize_wallet_funding,
            payload={
                "user_id": payload.user_id,
                "wallet_id": payload.wallet_id,
                "amount": payload.amount,
                "provider_name": payload.provider_name,
                "provider_reference": payload.provider_reference,
                "currency": payload.currency,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Wallet funding request initiated successfully.",
        )

    async def transfer(self, payload: WalletTransferRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet transfer requests."""
        # IDOR FIX: Validate authenticated user is the sender (not the recipient)
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.sender_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot transfer from another user's wallet.")

        return await self._execute(
            action="transfer",
            handler=self.wallet_service.transfer_between_users,
            payload={
                "sender_user_id": payload.sender_user_id,
                "sender_wallet_id": payload.sender_wallet_id,
                "recipient_user_id": payload.recipient_user_id,
                "recipient_wallet_id": payload.recipient_wallet_id,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "description": payload.description,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Wallet transfer completed successfully.",
        )

    async def create_transaction_pin(self, payload: TransactionPinRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transfer PIN creation requests."""
        # IDOR FIX: Validate authenticated user matches the user managing the PIN
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot manage PIN for another user.")

        return await self._execute(
            action="create_transaction_pin",
            handler=self.wallet_service.create_transaction_pin,
            payload={
                "user_id": payload.user_id,
                "pin": payload.pin,
                "confirm_pin": payload.confirm_pin,
                "require_complexity": payload.require_complexity,
            },
            success_message="Transaction PIN created successfully.",
        )

    async def update_transaction_pin(self, payload: TransactionPinRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transfer PIN update requests."""
        # IDOR FIX: Validate authenticated user matches the user managing the PIN
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot manage PIN for another user.")

        return await self._execute(
            action="update_transaction_pin",
            handler=self.wallet_service.change_transaction_pin,
            payload={
                "user_id": payload.user_id,
                "current_pin": payload.pin,
                "new_pin": payload.pin,
                "confirm_pin": payload.confirm_pin,
                "require_complexity": payload.require_complexity,
            },
            success_message="Transaction PIN updated successfully.",
        )

    async def verify_transaction_pin(self, payload: TransactionPinVerificationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transfer PIN verification requests."""
        # IDOR FIX: Validate authenticated user matches the user verifying the PIN
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot verify PIN for another user.")

        result = await self.wallet_service.verify_transaction_pin(user_id=payload.user_id, pin=payload.pin)
        return success_response(
            data={"verified": result},
            message="Transaction PIN verified successfully.",
        )

    async def reset_transaction_pin(self, payload: TransactionPinResetRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transfer PIN reset requests."""
        # IDOR FIX: Validate authenticated user matches the user resetting the PIN
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot reset PIN for another user.")

        return await self._execute(
            action="reset_transaction_pin",
            handler=self.wallet_service.reset_transaction_pin,
            payload={
                "user_id": payload.user_id,
                "new_pin": payload.new_pin,
                "confirm_pin": payload.confirm_pin,
                "require_complexity": payload.require_complexity,
            },
            success_message="Transaction PIN reset successfully.",
        )

    async def get_transaction_history(self, payload: TransactionHistoryRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction history retrieval requests."""
        # IDOR FIX: Validate authenticated user matches the user requesting the history
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot retrieve transaction history for another user.")

        return await self._execute(
            action="get_transaction_history",
            handler=self.wallet_service.get_transaction_history,
            payload={
                "user_id": payload.user_id,
                "wallet_id": payload.wallet_id,
                "page": payload.page,
                "page_size": payload.page_size,
            },
            success_message="Transaction history retrieved successfully.",
        )

    async def get_transaction_details(self, transaction_id: UUID, payload: TransactionDetailRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction detail retrieval requests."""
        # IDOR FIX: Validate authenticated user matches the user requesting the details
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot retrieve transaction details for another user.")

        return await self._execute(
            action="get_transaction_details",
            handler=self.wallet_service.get_transaction_details,
            payload={"user_id": payload.user_id, "transaction_id": transaction_id},
            success_message="Transaction details retrieved successfully.",
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

        log_api_event(self.logger, "wallet_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _get_authenticated_user_id(self, request: Request | None) -> UUID | None:
        """Extract and validate authenticated user_id from request context."""
        if request is None:
            return None
        
        # Try to get authenticated user from request.state set by AuthMiddleware
        auth_user = getattr(request.state, "auth_user", None)
        if auth_user is not None:
            try:
                user_id = getattr(auth_user, "user_id", None)
                if user_id:
                    return UUID(str(user_id))
            except (ValueError, TypeError, AttributeError):
                pass
        
        # Fallback to auth_payload
        auth_payload = getattr(request.state, "auth_payload", None)
        if isinstance(auth_payload, dict):
            raw_user_id = auth_payload.get("user_id") or auth_payload.get("sub")
            if raw_user_id:
                try:
                    return UUID(str(raw_user_id))
                except (ValueError, TypeError):
                    pass
        
        return None

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "wallet_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )
