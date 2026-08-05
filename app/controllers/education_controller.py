from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.education_service import EducationService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class EducationPurchaseRequest(BaseModel):
    """Request schema for education purchase requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the purchase.")
    examination_type: str = Field(..., description="Examination type such as WAEC, NECO, NABTEB, or JAMB.")
    provider: str = Field(..., description="Education service provider.")
    candidate_number: str = Field(..., description="Candidate or registration number.")
    amount: Decimal = Field(..., gt=0, description="Education service amount.")
    quantity: int = Field(..., ge=1, description="Number of units or pins to purchase.")
    examination_year: int | str = Field(..., description="Examination year.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the purchase.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    description: str | None = Field(default=None, description="Optional purchase description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class EducationValidationRequest(BaseModel):
    """Request schema for education service validation requests."""

    examination_type: str = Field(..., description="Examination type such as WAEC, NECO, NABTEB, or JAMB.")
    provider: str = Field(..., description="Education service provider.")
    candidate_number: str = Field(..., description="Candidate or registration number.")
    amount: Decimal = Field(..., gt=0, description="Education service amount.")
    quantity: int = Field(..., ge=1, description="Number of units or pins to purchase.")
    examination_year: int | str = Field(..., description="Examination year.")
    transaction_pin: str | None = Field(default=None, description="Optional transaction PIN.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")


class EducationPricingRequest(BaseModel):
    """Request schema for education pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Education service amount to price.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    examination_type: str | None = Field(default=None, description="Optional examination type.")
    promotion_code: str | None = Field(default=None, description="Optional promotion code.")


class EducationServiceRequest(BaseModel):
    """Request schema for available education service lookups."""

    provider_name: str | None = Field(default=None, description="Optional provider name.")


class EducationStatusRequest(BaseModel):
    """Request schema for education transaction status lookups."""

    reference: str = Field(..., min_length=1, description="Education transaction reference.")


class EducationReconciliationRequest(BaseModel):
    """Request schema for education reconciliation requests."""

    reference: str = Field(..., min_length=1, description="Education transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class EducationHistoryRequest(BaseModel):
    """Request schema for education purchase history requests."""

    reference: str = Field(..., min_length=1, description="Education transaction reference.")


class EducationDetailRequest(BaseModel):
    """Request schema for education transaction detail requests."""

    reference: str = Field(..., min_length=1, description="Education transaction reference.")


class EducationController:
    """Thin FastAPI controller for education endpoints."""

    def __init__(self, education_service: EducationService, logger: logging.Logger | None = None) -> None:
        self.education_service = education_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/education", tags=["Education"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/waec", status_code=status.HTTP_201_CREATED)(self.purchase_waec)
        self.router.post("/neco", status_code=status.HTTP_201_CREATED)(self.purchase_neco)
        self.router.post("/nabteb", status_code=status.HTTP_201_CREATED)(self.purchase_nabteb)
        self.router.post("/jamb", status_code=status.HTTP_201_CREATED)(self.purchase_jamb)
        self.router.post("/remita", status_code=status.HTTP_201_CREATED)(self.purchase_remita)
        self.router.post("/validate", status_code=status.HTTP_200_OK)(self.validate_service)
        self.router.get("/services", status_code=status.HTTP_200_OK)(self.get_services)
        self.router.post("/price", status_code=status.HTTP_200_OK)(self.get_price)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_status)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_purchase_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_purchase_details)

    async def purchase_waec(self, payload: EducationPurchaseRequest) -> dict[str, Any]:
        """Handle WAEC PIN purchase requests."""
        return await self._execute(
            action="purchase_waec",
            handler=self.education_service.purchase_education,
            payload={
                "user_id": payload.user_id,
                "examination_type": "WAEC",
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="WAEC purchase initiated successfully.",
        )

    async def purchase_neco(self, payload: EducationPurchaseRequest) -> dict[str, Any]:
        """Handle NECO PIN purchase requests."""
        return await self._execute(
            action="purchase_neco",
            handler=self.education_service.purchase_education,
            payload={
                "user_id": payload.user_id,
                "examination_type": "NECO",
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="NECO purchase initiated successfully.",
        )

    async def purchase_nabteb(self, payload: EducationPurchaseRequest) -> dict[str, Any]:
        """Handle NABTEB PIN purchase requests."""
        return await self._execute(
            action="purchase_nabteb",
            handler=self.education_service.purchase_education,
            payload={
                "user_id": payload.user_id,
                "examination_type": "NABTEB",
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="NABTEB purchase initiated successfully.",
        )

    async def purchase_jamb(self, payload: EducationPurchaseRequest) -> dict[str, Any]:
        """Handle JAMB e-PIN purchase requests."""
        return await self._execute(
            action="purchase_jamb",
            handler=self.education_service.purchase_education,
            payload={
                "user_id": payload.user_id,
                "examination_type": "JAMB",
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="JAMB purchase initiated successfully.",
        )

    async def purchase_remita(self, payload: EducationPurchaseRequest) -> dict[str, Any]:
        """Handle Remita payment requests where supported."""
        return await self._execute(
            action="purchase_remita",
            handler=self.education_service.purchase_education,
            payload={
                "user_id": payload.user_id,
                "examination_type": "REMITA",
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
                "currency": payload.currency,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Remita payment initiated successfully.",
        )

    async def validate_service(self, payload: EducationValidationRequest) -> dict[str, Any]:
        """Handle education service validation requests."""
        return await self._execute(
            action="validate_service",
            handler=self.education_service.validate_purchase_request,
            payload={
                "user_id": payload.user_id if hasattr(payload, 'user_id') else UUID(int=0),
                "examination_type": payload.examination_type,
                "provider": payload.provider,
                "candidate_number": payload.candidate_number,
                "amount": payload.amount,
                "quantity": payload.quantity,
                "examination_year": payload.examination_year,
                "transaction_pin": payload.transaction_pin,
                "wallet_id": payload.wallet_id,
            },
            success_message="Education service validated successfully.",
        )

    async def get_services(self, payload: EducationServiceRequest) -> dict[str, Any]:
        """Handle available education service requests."""
        return await self._execute(
            action="get_services",
            handler=self.education_service.validate_provider,
            payload={"provider": payload.provider_name},
            success_message="Education services retrieved successfully.",
        )

    async def get_price(self, payload: EducationPricingRequest) -> dict[str, Any]:
        """Handle education pricing requests."""
        return await self._execute(
            action="get_price",
            handler=self.education_service.calculate_pricing,
            payload={
                "amount": payload.amount,
                "provider_name": payload.provider_name,
                "examination_type": payload.examination_type,
                "promotion_code": payload.promotion_code,
            },
            success_message="Education pricing retrieved successfully.",
        )

    async def get_purchase_status(self, reference: str) -> dict[str, Any]:
        """Handle education transaction status requests."""
        return await self._execute(
            action="get_purchase_status",
            handler=self.education_service.get_purchase_status,
            payload={"reference": reference},
            success_message="Education transaction status retrieved successfully.",
        )

    async def reconcile_transaction(self, payload: EducationReconciliationRequest) -> dict[str, Any]:
        """Handle education reconciliation requests."""
        return await self._execute(
            action="reconcile_transaction",
            handler=self.education_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="Education transaction reconciled successfully.",
        )

    async def get_purchase_history(self, payload: EducationHistoryRequest) -> dict[str, Any]:
        """Handle education purchase history requests."""
        return await self._execute(
            action="get_purchase_history",
            handler=self.education_service.get_purchase_status,
            payload={"reference": payload.reference},
            success_message="Education purchase history retrieved successfully.",
        )

    async def get_purchase_details(self, reference: str) -> dict[str, Any]:
        """Handle education transaction detail requests."""
        return await self._execute(
            action="get_purchase_details",
            handler=self.education_service.get_purchase_details,
            payload={"reference": reference},
            success_message="Education transaction details retrieved successfully.",
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

        log_api_event(self.logger, "education_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "education_request_failed", action=action, error=str(exc))
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
