from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.services.data_service import DataService
from app.utils.exceptions import AppException, AuthorizationException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class DataPurchaseRequest(BaseModel):
    """Request schema for data purchase requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the purchase.")
    phone_number: str = Field(..., description="Recipient phone number.")
    amount: Decimal = Field(..., gt=0, description="Data amount to purchase.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the purchase.")
    network: str | None = Field(default=None, description="Optional network operator name, such as MTN or Glo.")
    plan_id: str | None = Field(default=None, description="Optional data plan identifier.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional purchase description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class DataPlansRequest(BaseModel):
    """Request schema for data plan lookups."""

    network: str | None = Field(default=None, description="Optional network operator.")
    force_refresh: bool = Field(default=False, description="Whether to force a fresh plan refresh.")


class DataPlanValidationRequest(BaseModel):
    """Request schema for data plan validation requests."""

    plan_id: str = Field(..., min_length=1, description="Data plan identifier.")


class DataPricingRequest(BaseModel):
    """Request schema for data pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Data amount to price.")
    network: str | None = Field(default=None, description="Optional network operator.")


class DataStatusRequest(BaseModel):
    """Request schema for data transaction status lookups."""

    reference: str = Field(..., min_length=1, description="Data transaction reference.")


class DataReconciliationRequest(BaseModel):
    """Request schema for data reconciliation requests."""

    reference: str = Field(..., min_length=1, description="Data transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class DataHistoryRequest(BaseModel):
    """Request schema for data purchase history requests."""

    reference: str = Field(..., min_length=1, description="Data transaction reference.")


class DataDetailRequest(BaseModel):
    """Request schema for data transaction detail requests."""

    reference: str = Field(..., min_length=1, description="Data transaction reference.")


class DataController:
    """Thin FastAPI controller for data endpoints."""

    def __init__(self, data_service: DataService, logger: logging.Logger | None = None) -> None:
        self.data_service = data_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/data", tags=["Data"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/purchase", status_code=status.HTTP_201_CREATED)(self.purchase_data)
        self.router.get("/plans", status_code=status.HTTP_200_OK)(self.get_data_plans)
        self.router.post("/plans/validate", status_code=status.HTTP_200_OK)(self.validate_plan)
        self.router.post("/price", status_code=status.HTTP_200_OK)(self.get_price)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_status)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_purchase_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_details)

    async def purchase_data(self, payload: DataPurchaseRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle data purchase requests."""
        # IDOR FIX: Validate authenticated user matches the user making the purchase
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        if payload.user_id != authenticated_user_id:
            raise AuthorizationException("Cannot purchase data for another user.")

        return await self._execute(
            action="purchase_data",
            handler=self.data_service.purchase_data,
            payload={
                "user_id": payload.user_id,
                "phone_number": payload.phone_number,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "network": payload.network,
                "plan_id": payload.plan_id,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Data purchase initiated successfully.",
        )

    async def get_data_plans(self, payload: DataPlansRequest) -> dict[str, Any]:
        """Handle available data plans requests."""
        return await self._execute(
            action="get_data_plans",
            handler=self.data_service.get_data_plans,
            payload={"network": payload.network, "force_refresh": payload.force_refresh},
            success_message="Data plans retrieved successfully.",
        )

    async def validate_plan(self, payload: DataPlanValidationRequest) -> dict[str, Any]:
        """Handle data plan validation requests."""
        return await self._execute(
            action="validate_plan",
            handler=self.data_service.get_plan_by_id,
            payload={"plan_id": payload.plan_id},
            success_message="Data plan validated successfully.",
        )

    async def get_price(self, payload: DataPricingRequest) -> dict[str, Any]:
        """Handle data pricing requests."""
        return await self._execute(
            action="get_price",
            handler=self.data_service.calculate_price,
            payload={"amount": payload.amount, "network": payload.network},
            success_message="Data price retrieved successfully.",
        )

    async def get_purchase_status(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle data transaction status requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.data_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_status",
            handler=self.data_service.get_purchase_status,
            payload={"reference": reference},
            success_message="Data transaction status retrieved successfully.",
        )

    async def reconcile_transaction(self, payload: DataReconciliationRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle data reconciliation requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.data_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="reconcile_transaction",
            handler=self.data_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="Data transaction reconciled successfully.",
        )

    async def get_purchase_history(self, payload: DataHistoryRequest, request: Request | None = None) -> dict[str, Any]:
        """Handle data purchase history requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.data_service.get_transaction_user_id_by_reference(payload.reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_history",
            handler=self.data_service.get_purchase_status,
            payload={"reference": payload.reference},
            success_message="Data purchase history retrieved successfully.",
        )

    async def get_purchase_details(self, reference: str, request: Request | None = None) -> dict[str, Any]:
        """Handle data transaction detail requests."""
        # IDOR FIX: Verify user owns the transaction
        authenticated_user_id = self._get_authenticated_user_id(request)
        if authenticated_user_id is None:
            raise AuthorizationException("Authentication required.")
        transaction_user_id = await self.data_service.get_transaction_user_id_by_reference(reference)
        if transaction_user_id is not None and transaction_user_id != authenticated_user_id:
            raise AuthorizationException("Cannot access transaction belonging to another user.")
        
        return await self._execute(
            action="get_purchase_details",
            handler=self.data_service.get_purchase_details,
            payload={"reference": reference},
            success_message="Data transaction details retrieved successfully.",
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

        log_api_event(self.logger, "data_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "data_request_failed", action=action, error=str(exc))
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
