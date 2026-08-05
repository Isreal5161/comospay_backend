from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.electricity_service import ElectricityService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class ElectricityPurchaseRequest(BaseModel):
    """Request schema for electricity purchase requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the purchase.")
    meter_number: str = Field(..., description="Electricity meter number.")
    disco: str = Field(..., description="Distribution company code or name.")
    amount: Decimal = Field(..., gt=0, description="Electricity bill amount to purchase.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the purchase.")
    meter_type: str | None = Field(default=None, description="Optional meter type such as prepaid or postpaid.")
    customer_name: str | None = Field(default=None, description="Optional customer name.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional purchase description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class MeterValidationRequest(BaseModel):
    """Request schema for meter validation requests."""

    meter_number: str = Field(..., description="Electricity meter number.")
    disco: str = Field(..., description="Distribution company code or name.")
    meter_type: str | None = Field(default=None, description="Optional meter type such as prepaid or postpaid.")


class ElectricityPricingRequest(BaseModel):
    """Request schema for electricity pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Electricity bill amount to price.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    promotion_code: str | None = Field(default=None, description="Optional promotion code.")
    disco: str | None = Field(default=None, description="Optional distribution company.")
    meter_type: str | None = Field(default=None, description="Optional meter type.")


class ProvidersRequest(BaseModel):
    """Request schema for available electricity provider lookups."""

    disco: str | None = Field(default=None, description="Optional distribution company.")


class ElectricityStatusRequest(BaseModel):
    """Request schema for electricity transaction status lookups."""

    reference: str = Field(..., min_length=1, description="Electricity transaction reference.")


class ElectricityReconciliationRequest(BaseModel):
    """Request schema for electricity reconciliation requests."""

    reference: str = Field(..., min_length=1, description="Electricity transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class ElectricityHistoryRequest(BaseModel):
    """Request schema for electricity purchase history requests."""

    reference: str = Field(..., min_length=1, description="Electricity transaction reference.")


class ElectricityDetailRequest(BaseModel):
    """Request schema for electricity transaction detail requests."""

    reference: str = Field(..., min_length=1, description="Electricity transaction reference.")


class ElectricityController:
    """Thin FastAPI controller for electricity endpoints."""

    def __init__(self, electricity_service: ElectricityService, logger: logging.Logger | None = None) -> None:
        self.electricity_service = electricity_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/electricity", tags=["Electricity"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/purchase", status_code=status.HTTP_201_CREATED)(self.purchase_electricity)
        self.router.post("/meter/validate", status_code=status.HTTP_200_OK)(self.validate_meter)
        self.router.post("/price", status_code=status.HTTP_200_OK)(self.get_price)
        self.router.get("/providers", status_code=status.HTTP_200_OK)(self.get_providers)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_status)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_purchase_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_details)

    async def purchase_electricity(self, payload: ElectricityPurchaseRequest) -> dict[str, Any]:
        """Handle electricity bill purchase requests."""
        return await self._execute(
            action="purchase_electricity",
            handler=self.electricity_service.purchase_electricity,
            payload={
                "user_id": payload.user_id,
                "meter_number": payload.meter_number,
                "disco": payload.disco,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "meter_type": payload.meter_type,
                "customer_name": payload.customer_name,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Electricity purchase initiated successfully.",
        )

    async def validate_meter(self, payload: MeterValidationRequest) -> dict[str, Any]:
        """Handle meter validation requests."""
        return await self._execute(
            action="validate_meter",
            handler=self.electricity_service.verify_meter,
            payload={
                "meter_number": payload.meter_number,
                "disco": payload.disco,
                "meter_type": payload.meter_type,
            },
            success_message="Meter validated successfully.",
        )

    async def get_price(self, payload: ElectricityPricingRequest) -> dict[str, Any]:
        """Handle electricity pricing requests."""
        return await self._execute(
            action="get_price",
            handler=self.electricity_service.calculate_pricing,
            payload={
                "amount": payload.amount,
                "provider_name": payload.provider_name,
                "promotion_code": payload.promotion_code,
                "disco": payload.disco,
                "meter_type": payload.meter_type,
            },
            success_message="Electricity pricing retrieved successfully.",
        )

    async def get_providers(self, payload: ProvidersRequest) -> dict[str, Any]:
        """Handle available electricity providers requests."""
        return await self._execute(
            action="get_providers",
            handler=self.electricity_service.verify_meter,
            payload={"meter_number": "", "disco": payload.disco or "", "meter_type": None},
            success_message="Electricity providers retrieved successfully.",
        )

    async def get_purchase_status(self, reference: str) -> dict[str, Any]:
        """Handle electricity transaction status requests."""
        return await self._execute(
            action="get_purchase_status",
            handler=self.electricity_service.get_purchase_status,
            payload={"reference": reference},
            success_message="Electricity transaction status retrieved successfully.",
        )

    async def reconcile_transaction(self, payload: ElectricityReconciliationRequest) -> dict[str, Any]:
        """Handle electricity reconciliation requests."""
        return await self._execute(
            action="reconcile_transaction",
            handler=self.electricity_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="Electricity transaction reconciled successfully.",
        )

    async def get_purchase_history(self, payload: ElectricityHistoryRequest) -> dict[str, Any]:
        """Handle electricity purchase history requests."""
        return await self._execute(
            action="get_purchase_history",
            handler=self.electricity_service.get_purchase_status,
            payload={"reference": payload.reference},
            success_message="Electricity purchase history retrieved successfully.",
        )

    async def get_purchase_details(self, reference: str) -> dict[str, Any]:
        """Handle electricity transaction detail requests."""
        return await self._execute(
            action="get_purchase_details",
            handler=self.electricity_service.get_purchase_details,
            payload={"reference": reference},
            success_message="Electricity transaction details retrieved successfully.",
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

        log_api_event(self.logger, "electricity_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "electricity_request_failed", action=action, error=str(exc))
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
