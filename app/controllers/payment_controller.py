from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.payment_service import PaymentService
from app.utils.exceptions import AppException, AuthorizationException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class PaymentInitializeRequest(BaseModel):
    """Request schema for payment initialization."""

    user_id: UUID | None = Field(default=None, description="Identifier of the paying user.")
    amount: Decimal = Field(..., gt=0, description="Payment amount.")
    reference: str = Field(..., min_length=1, description="Unique payment reference.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional payment description.")
    redirect_url: str | None = Field(default=None, description="Optional redirect URL after payment completion.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")
    transaction_type: str = Field(default="payment", description="Payment transaction type.")
    category: str = Field(default="payment", description="Payment category.")


class PaymentCollectionRequest(PaymentInitializeRequest):
    """Request schema for payment collection requests."""

    transaction_type: str = Field(default="collection", description="Collection transaction type.")
    category: str = Field(default="collection", description="Collection category.")


class PaymentVerificationRequest(BaseModel):
    """Request schema for payment verification."""

    reference: str = Field(..., min_length=1, description="Payment reference to verify.")


class PaymentStatusRequest(BaseModel):
    """Request schema for payment status lookups."""

    reference: str = Field(..., min_length=1, description="Payment reference to inspect.")


class PaymentReconciliationRequest(BaseModel):
    """Request schema for payment reconciliation."""

    reference: str = Field(..., min_length=1, description="Payment reference to reconcile.")
    provider_status: str | None = Field(default=None, description="Optional provider-reported status.")


class PaymentCancellationRequest(BaseModel):
    """Request schema for payment cancellation."""

    reference: str = Field(..., min_length=1, description="Payment reference to cancel.")
    reason: str | None = Field(default=None, description="Optional cancellation reason.")


class PaymentHistoryRequest(BaseModel):
    """Request schema for payment history lookups."""

    reference: str = Field(..., min_length=1, description="Payment reference to inspect.")


class PaymentDetailRequest(BaseModel):
    """Request schema for payment detail lookups."""

    reference: str = Field(..., min_length=1, description="Payment reference to inspect.")


class PaymentController:
    """Thin FastAPI controller for payment endpoints."""

    def __init__(self, payment_service: PaymentService, logger: logging.Logger | None = None) -> None:
        self.payment_service = payment_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/payments", tags=["Payments"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.add_api_route("", self._initialize_payment_route, methods=["POST"], status_code=status.HTTP_201_CREATED, response_model=None)
        self.router.add_api_route("/collect", self._collect_payment_route, methods=["POST"], status_code=status.HTTP_201_CREATED, response_model=None)
        self.router.add_api_route("/verify", self._verify_payment_route, methods=["POST"], status_code=status.HTTP_200_OK, response_model=None)
        self.router.add_api_route("/status/{reference}", self._get_payment_status_route, methods=["GET"], status_code=status.HTTP_200_OK, response_model=None)
        self.router.add_api_route("/reconcile", self._reconcile_payment_route, methods=["POST"], status_code=status.HTTP_200_OK, response_model=None)
        self.router.add_api_route("/cancel", self._cancel_payment_route, methods=["POST"], status_code=status.HTTP_200_OK, response_model=None)
        self.router.add_api_route("/history", self._get_payment_history_route, methods=["GET"], status_code=status.HTTP_200_OK, response_model=None)
        self.router.add_api_route("/history/{reference}", self._get_payment_details_route, methods=["GET"], status_code=status.HTTP_200_OK, response_model=None)

    async def _initialize_payment_route(self, payload: PaymentInitializeRequest, request: Request) -> dict[str, Any]:
        return await self.initialize_payment(payload, request=request)

    async def _collect_payment_route(self, payload: PaymentCollectionRequest, request: Request) -> dict[str, Any]:
        return await self.collect_payment(payload, request=request)

    async def _verify_payment_route(self, payload: PaymentVerificationRequest, request: Request) -> dict[str, Any]:
        return await self.verify_payment(payload, request=request)

    async def _get_payment_status_route(self, reference: str, request: Request) -> dict[str, Any]:
        return await self.get_payment_status(reference, request=request)

    async def _reconcile_payment_route(self, payload: PaymentReconciliationRequest, request: Request) -> dict[str, Any]:
        return await self.reconcile_payment(payload, request=request)

    async def _cancel_payment_route(self, payload: PaymentCancellationRequest, request: Request) -> dict[str, Any]:
        return await self.cancel_payment(payload, request=request)

    async def _get_payment_history_route(self, payload: PaymentHistoryRequest, request: Request) -> dict[str, Any]:
        return await self.get_payment_history(payload, request=request)

    async def _get_payment_details_route(self, reference: str, request: Request) -> dict[str, Any]:
        return await self.get_payment_details(reference, request=request)

    async def initialize_payment(self, payload: PaymentInitializeRequest, request: Request | None = None, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle payment initialization requests."""
        authenticated_user_id = None
        if request is not None:
            authenticated_user_id = self._get_authenticated_user_id(request)
            if authenticated_user_id is None:
                raise AuthorizationException("Authentication required.")
            if payload.user_id is not None and payload.user_id != authenticated_user_id:
                raise AuthorizationException("Cannot initialize payment for another user.")

        # Resolve target user: explicit param > payload > authenticated context
        target_user_id = user_id or payload.user_id or authenticated_user_id or self._resolve_user_id()

        return await self._execute(
            action="initialize_payment",
            handler=self.payment_service.initialize_payment,
            payload={
                "user_id": target_user_id,
                "amount": payload.amount,
                "reference": payload.reference,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "redirect_url": payload.redirect_url,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
                "transaction_type": payload.transaction_type,
                "category": payload.category,
            },
            success_message="Payment initialized successfully.",
        )

    async def collect_payment(self, payload: PaymentCollectionRequest, request: Request | None = None, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle payment collection requests."""
        authenticated_user_id = None
        if request is not None:
            authenticated_user_id = self._get_authenticated_user_id(request)
            if authenticated_user_id is None:
                raise AuthorizationException("Authentication required.")
            if payload.user_id is not None and payload.user_id != authenticated_user_id:
                raise AuthorizationException("Cannot collect payment for another user.")

        # Resolve target user: explicit param > payload > authenticated context
        target_user_id = user_id or payload.user_id or authenticated_user_id or self._resolve_user_id()

        return await self._execute(
            action="collect_payment",
            handler=self.payment_service.initialize_payment,
            payload={
                "user_id": target_user_id,
                "amount": payload.amount,
                "reference": payload.reference,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "redirect_url": payload.redirect_url,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
                "transaction_type": payload.transaction_type,
                "category": payload.category,
            },
            success_message="Payment collection initialized successfully.",
        )

    async def verify_payment(self, payload: PaymentVerificationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle payment verification requests."""
        if request is None:
            return await self._execute(
                action="verify_payment",
                handler=self.payment_service.verify_payment,
                payload={"reference": payload.reference},
                success_message="Payment verified successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="verify_payment",
            handler=self.payment_service.verify_payment,
            payload={"reference": payload.reference},
            success_message="Payment verified successfully.",
        )

    async def get_payment_status(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle payment status requests."""
        if request is None:
            return await self._execute(
                action="get_payment_status",
                handler=self.payment_service.get_payment_status,
                payload={"reference": reference},
                success_message="Payment status retrieved successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_payment_status",
            handler=self.payment_service.get_payment_status,
            payload={"reference": reference},
            success_message="Payment status retrieved successfully.",
        )

    async def reconcile_payment(self, payload: PaymentReconciliationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle payment reconciliation requests."""
        if request is None:
            return await self._execute(
                action="reconcile_payment",
                handler=self.payment_service.reconcile_payment,
                payload={"reference": payload.reference, "provider_status": payload.provider_status},
                success_message="Payment reconciliation completed successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="reconcile_payment",
            handler=self.payment_service.reconcile_payment,
            payload={"reference": payload.reference, "provider_status": payload.provider_status},
            success_message="Payment reconciliation completed successfully.",
        )

    async def cancel_payment(self, payload: PaymentCancellationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle payment cancellation requests."""
        if request is None:
            return await self._execute(
                action="cancel_payment",
                handler=self.payment_service.cancel_payment,
                payload={"reference": payload.reference, "reason": payload.reason},
                success_message="Payment cancelled successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="cancel_payment",
            handler=self.payment_service.cancel_payment,
            payload={"reference": payload.reference, "reason": payload.reason},
            success_message="Payment cancelled successfully.",
        )

    async def get_payment_history(self, payload: PaymentHistoryRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle payment history requests."""
        if request is None:
            return await self._execute(
                action="get_payment_history",
                handler=self.payment_service.get_payment_status,
                payload={"reference": payload.reference},
                success_message="Payment history retrieved successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_payment_history",
            handler=self.payment_service.get_payment_status,
            payload={"reference": payload.reference},
            success_message="Payment history retrieved successfully.",
        )

    async def get_payment_details(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle payment detail requests."""
        if request is None:
            return await self._execute(
                action="get_payment_details",
                handler=self.payment_service.get_payment_status,
                payload={"reference": reference},
                success_message="Payment details retrieved successfully.",
            )

        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.payment_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_payment_details",
            handler=self.payment_service.get_payment_status,
            payload={"reference": reference},
            success_message="Payment details retrieved successfully.",
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

        log_api_event(self.logger, "payment_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "payment_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )

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

    def _resolve_user_id(self) -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
