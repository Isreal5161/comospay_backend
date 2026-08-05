from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.airtime_service import AirtimeService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class AirtimePurchaseRequest(BaseModel):
    """Request schema for airtime purchase requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the purchase.")
    phone_number: str = Field(..., description="Recipient phone number.")
    network: str | None = Field(default=None, description="Optional network operator name, such as MTN or Glo.")
    amount: Decimal = Field(..., gt=0, description="Airtime amount to purchase.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the purchase.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional purchase description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class AirtimeValidationRequest(BaseModel):
    """Request schema for airtime validation requests."""

    user_id: UUID = Field(..., description="Identifier of the user validating the purchase.")
    phone_number: str = Field(..., description="Recipient phone number.")
    amount: Decimal = Field(..., gt=0, description="Airtime amount to validate.")
    network: str | None = Field(default=None, description="Optional network operator name, such as MTN or Glo.")
    transaction_pin: str | None = Field(default=None, description="Optional transaction PIN.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")


class AirtimePricingRequest(BaseModel):
    """Request schema for airtime pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Airtime amount to price.")
    network: str | None = Field(default=None, description="Optional network operator.")


class AirtimeStatusRequest(BaseModel):
    """Request schema for airtime transaction status lookups."""

    reference: str = Field(..., min_length=1, description="Airtime transaction reference.")


class AirtimeReconciliationRequest(BaseModel):
    """Request schema for airtime reconciliation requests."""

    reference: str = Field(..., min_length=1, description="Airtime transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class AirtimeHistoryRequest(BaseModel):
    """Request schema for airtime purchase history requests."""

    reference: str = Field(..., min_length=1, description="Airtime transaction reference.")


class AirtimeDetailRequest(BaseModel):
    """Request schema for airtime transaction detail requests."""

    reference: str = Field(..., min_length=1, description="Airtime transaction reference.")


class AirtimeController:
    """Thin FastAPI controller for airtime endpoints."""

    def __init__(self, airtime_service: AirtimeService, logger: logging.Logger | None = None) -> None:
        self.airtime_service = airtime_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/airtime", tags=["Airtime"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/purchase", status_code=status.HTTP_201_CREATED)(self.purchase_airtime)
        self.router.post("/validate", status_code=status.HTTP_200_OK)(self.validate_purchase_request)
        self.router.post("/price", status_code=status.HTTP_200_OK)(self.get_price)
        self.router.post("/pricing", status_code=status.HTTP_200_OK)(self.get_pricing_breakdown)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_status)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_purchase_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_details)

    async def purchase_airtime(self, payload: AirtimePurchaseRequest) -> dict[str, Any]:
        """Handle airtime purchase requests."""
        return await self._execute(
            action="purchase_airtime",
            handler=self.airtime_service.purchase_airtime,
            payload={
                "user_id": payload.user_id,
                "phone_number": payload.phone_number,
                "network": payload.network,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Airtime purchase initiated successfully.",
        )

    async def validate_purchase_request(self, payload: AirtimeValidationRequest) -> dict[str, Any]:
        """Handle airtime validation requests."""
        return await self._execute(
            action="validate_purchase_request",
            handler=self.airtime_service.validate_purchase_request,
            payload={
                "user_id": payload.user_id,
                "phone_number": payload.phone_number,
                "amount": payload.amount,
                "network": payload.network,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
            },
            success_message="Airtime purchase request validated successfully.",
        )

    async def get_price(self, payload: AirtimePricingRequest) -> dict[str, Any]:
        """Handle airtime pricing requests."""
        return await self._execute(
            action="get_price",
            handler=self.airtime_service.calculate_price,
            payload={"amount": payload.amount, "network": payload.network},
            success_message="Airtime price retrieved successfully.",
        )

    async def get_pricing_breakdown(self, payload: AirtimePricingRequest) -> dict[str, Any]:
        """Handle airtime pricing breakdown requests."""
        return await self._execute(
            action="get_pricing_breakdown",
            handler=self.airtime_service.calculate_final_amount,
            payload={"amount": payload.amount, "network": payload.network},
            success_message="Airtime pricing breakdown retrieved successfully.",
        )

    async def get_purchase_status(self, reference: str) -> dict[str, Any]:
        """Handle airtime transaction status requests."""
        return await self._execute(
            action="get_purchase_status",
            handler=self.airtime_service.get_purchase_status,
            payload={"reference": reference},
            success_message="Airtime transaction status retrieved successfully.",
        )

    async def reconcile_transaction(self, payload: AirtimeReconciliationRequest) -> dict[str, Any]:
        """Handle airtime reconciliation requests."""
        return await self._execute(
            action="reconcile_transaction",
            handler=self.airtime_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="Airtime transaction reconciled successfully.",
        )

    async def get_purchase_history(self, payload: AirtimeHistoryRequest) -> dict[str, Any]:
        """Handle airtime purchase history requests."""
        return await self._execute(
            action="get_purchase_history",
            handler=self.airtime_service.get_purchase_status,
            payload={"reference": payload.reference},
            success_message="Airtime purchase history retrieved successfully.",
        )

    async def get_purchase_details(self, reference: str) -> dict[str, Any]:
        """Handle airtime transaction detail requests."""
        return await self._execute(
            action="get_purchase_details",
            handler=self.airtime_service.get_purchase_details,
            payload={"reference": reference},
            success_message="Airtime transaction details retrieved successfully.",
        )

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

        log_api_event(self.logger, "airtime_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "airtime_request_failed", action=action, error=str(exc))
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
