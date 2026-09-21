from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.tv_service import TVService
from app.utils.exceptions import AppException, AuthorizationException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class TVSubscriptionRequest(BaseModel):
    """Request schema for TV subscription requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the subscription.")
    provider: str = Field(..., description="TV provider such as DSTV, GOTV, or Startimes.")
    smart_card_number: str = Field(..., description="Smart card or IUC number.")
    amount: Decimal = Field(..., gt=0, description="Subscription amount.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the subscription.")
    package_code: str | None = Field(default=None, description="Optional bouquet package code.")
    service_type: str | None = Field(default=None, description="Optional service type.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional subscription description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class TVValidationRequest(BaseModel):
    """Request schema for smart card / IUC validation requests."""

    provider: str = Field(..., description="TV provider such as DSTV, GOTV, or Startimes.")
    smart_card_number: str = Field(..., description="Smart card or IUC number.")


class TVPackageRequest(BaseModel):
    """Request schema for bouquet listing requests."""

    provider_name: str | None = Field(default=None, description="Optional TV provider.")
    force_refresh: bool = Field(default=False, description="Whether to force a fresh bouquet refresh.")


class TVPricingRequest(BaseModel):
    """Request schema for bouquet pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Subscription amount to price.")
    provider_name: str | None = Field(default=None, description="Optional TV provider.")
    service_type: str | None = Field(default=None, description="Optional service type.")
    promotion_code: str | None = Field(default=None, description="Optional promotion code.")


class TVRenewalRequest(BaseModel):
    """Request schema for subscription renewal requests."""

    reference: str = Field(..., min_length=1, description="Existing TV transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class TVProviderRequest(BaseModel):
    """Request schema for available TV provider lookups."""

    provider_name: str | None = Field(default=None, description="Optional TV provider.")


class TVStatusRequest(BaseModel):
    """Request schema for TV transaction status lookups."""

    reference: str = Field(..., min_length=1, description="TV transaction reference.")


class TVReconciliationRequest(BaseModel):
    """Request schema for TV reconciliation requests."""

    reference: str = Field(..., min_length=1, description="TV transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class TVHistoryRequest(BaseModel):
    """Request schema for TV purchase history requests."""

    reference: str = Field(..., min_length=1, description="TV transaction reference.")


class TVDetailRequest(BaseModel):
    """Request schema for TV transaction detail requests."""

    reference: str = Field(..., min_length=1, description="TV transaction reference.")


class TVController:
    """Thin FastAPI controller for TV subscription endpoints."""

    def __init__(self, tv_service: TVService, logger: logging.Logger | None = None) -> None:
        self.tv_service = tv_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/tv", tags=["TV"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/subscribe", status_code=status.HTTP_201_CREATED)(self.purchase_tv)
        self.router.post("/validate", status_code=status.HTTP_200_OK)(self.validate_smart_card)
        self.router.get("/packages", status_code=status.HTTP_200_OK)(self.get_packages)
        self.router.post("/price", status_code=status.HTTP_200_OK)(self.get_price)
        self.router.post("/renew", status_code=status.HTTP_200_OK)(self.renew_subscription)
        self.router.get("/providers", status_code=status.HTTP_200_OK)(self.get_providers)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_status)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_purchase_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_details)

    async def purchase_tv(self, payload: TVSubscriptionRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle TV subscription purchase requests."""
        # IDOR FIX: Validate authenticated user matches the user making the subscription
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot subscribe to TV service for another user.")
        
        return await self._execute(
            action="purchase_tv",
            handler=self.tv_service.purchase_tv,
            payload={
                "user_id": payload.user_id,
                "provider": payload.provider,
                "smart_card_number": payload.smart_card_number,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "package_code": payload.package_code,
                "service_type": payload.service_type,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="TV subscription initiated successfully.",
        )

    async def validate_smart_card(self, payload: TVValidationRequest) -> dict[str, Any]:
        """Handle smart card / IUC validation requests."""
        return await self._execute(
            action="validate_smart_card",
            handler=self.tv_service.validate_smart_card_number,
            payload={"smart_card_number": payload.smart_card_number},
            success_message="Smart card validated successfully.",
        )

    async def get_packages(self, payload: TVPackageRequest) -> dict[str, Any]:
        """Handle bouquet listing requests."""
        return await self._execute(
            action="get_packages",
            handler=self.tv_service.get_tv_packages,
            payload={"provider_name": payload.provider_name, "force_refresh": payload.force_refresh},
            success_message="TV packages retrieved successfully.",
        )

    async def get_price(self, payload: TVPricingRequest) -> dict[str, Any]:
        """Handle bouquet pricing requests."""
        return await self._execute(
            action="get_price",
            handler=self.tv_service.calculate_pricing,
            payload={
                "amount": payload.amount,
                "provider_name": payload.provider_name,
                "service_type": payload.service_type,
                "promotion_code": payload.promotion_code,
            },
            success_message="TV pricing retrieved successfully.",
        )

    async def renew_subscription(self, payload: TVRenewalRequest) -> dict[str, Any]:
        """Handle subscription renewal requests."""
        return await self._execute(
            action="renew_subscription",
            handler=self.tv_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="TV subscription renewal requested successfully.",
        )

    async def get_providers(self, payload: TVProviderRequest) -> dict[str, Any]:
        """Handle available TV provider requests."""
        return await self._execute(
            action="get_providers",
            handler=self.tv_service.validate_provider,
            payload={"provider": payload.provider_name},
            success_message="TV providers retrieved successfully.",
        )

    async def get_purchase_status(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle TV transaction status requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.tv_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_status",
            handler=self.tv_service.get_purchase_status,
            payload={"reference": reference},
            success_message="TV transaction status retrieved successfully.",
        )

    async def reconcile_transaction(self, payload: TVReconciliationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle TV reconciliation requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.tv_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="reconcile_transaction",
            handler=self.tv_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="TV transaction reconciled successfully.",
        )

    async def get_purchase_history(self, payload: TVHistoryRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle TV purchase history requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.tv_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_history",
            handler=self.tv_service.get_purchase_status,
            payload={"reference": payload.reference},
            success_message="TV purchase history retrieved successfully.",
        )

    async def get_purchase_details(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle TV transaction detail requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.tv_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_details",
            handler=self.tv_service.get_purchase_details,
            payload={"reference": reference},
            success_message="TV transaction details retrieved successfully.",
        )

    def _get_authenticated_user_id(self, request: Request | None) -> UUID | None:
        """Extract and validate authenticated user_id from request context."""
        if request is None:
            return None
        auth_user = getattr(request.state, "auth_user", None)
        if auth_user is not None:
            try:
                user_id = getattr(auth_user, "user_id", None)
                if user_id:
                    return UUID(str(user_id))
            except (ValueError, TypeError, AttributeError):
                pass
        auth_payload = getattr(request.state, "auth_payload", None)
        if isinstance(auth_payload, dict):
            raw_user_id = auth_payload.get("user_id") or auth_payload.get("sub")
            if raw_user_id:
                try:
                    return UUID(str(raw_user_id))
                except (ValueError, TypeError):
                    pass
        return None

    async def _execute(
        self,
        action: str,
        handler: Callable[..., Awaitable[Any]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            result = await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

        log_api_event(self.logger, "tv_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "tv_request_failed", action=action, error=str(exc))
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
