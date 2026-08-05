from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.giftcard_service import GiftCardService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class GiftCardValuationRequest(BaseModel):
    """Request schema for gift card valuation requests."""

    amount: Decimal = Field(..., gt=0, description="Gift card amount.")
    brand: str = Field(..., description="Gift card brand.")
    card_type: str = Field(..., description="Gift card type.")
    country: str | None = Field(default=None, description="Optional country.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    promotion_code: str | None = Field(default=None, description="Optional promotion code.")


class GiftCardTradeRequest(BaseModel):
    """Request schema for gift card trading requests."""

    user_id: UUID = Field(..., description="Identifier of the user making the trade.")
    brand: str = Field(..., description="Gift card brand.")
    card_type: str = Field(..., description="Gift card type.")
    amount: Decimal = Field(..., gt=0, description="Gift card amount.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the trade.")
    country: str | None = Field(default=None, description="Optional country.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    description: str | None = Field(default=None, description="Optional trade description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class GiftCardPricingRequest(BaseModel):
    """Request schema for gift card pricing requests."""

    amount: Decimal = Field(..., gt=0, description="Gift card amount.")
    brand: str = Field(..., description="Gift card brand.")
    card_type: str = Field(..., description="Gift card type.")
    country: str | None = Field(default=None, description="Optional country.")
    denomination: str | None = Field(default=None, description="Optional denomination.")
    card_format: str | None = Field(default=None, description="Optional card format.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    promotion_code: str | None = Field(default=None, description="Optional promotion code.")


class GiftCardInitiationRequest(BaseModel):
    """Request schema for gift card transaction initiation requests."""

    user_id: UUID = Field(..., description="Identifier of the user initiating the transaction.")
    brand: str = Field(..., description="Gift card brand.")
    card_type: str = Field(..., description="Gift card type.")
    amount: Decimal = Field(..., gt=0, description="Gift card amount.")
    transaction_pin: str = Field(..., min_length=4, description="Transaction PIN used to authorize the transaction.")
    country: str | None = Field(default=None, description="Optional country.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    wallet_id: UUID | None = Field(default=None, description="Optional wallet identifier.")
    description: str | None = Field(default=None, description="Optional transaction description.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    metadata_payload: str | None = Field(default=None, description="Optional metadata payload.")


class GiftCardSettlementRequest(BaseModel):
    """Request schema for gift card settlement requests."""

    reference: str = Field(..., min_length=1, description="Gift card transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")
    partial_amount: Decimal | None = Field(default=None, gt=0, description="Optional partial settlement amount.")
    settlement_reference: str | None = Field(default=None, description="Optional settlement reference.")


class GiftCardReconciliationRequest(BaseModel):
    """Request schema for gift card reconciliation requests."""

    reference: str = Field(..., min_length=1, description="Gift card transaction reference.")
    provider_name: str | None = Field(default=None, description="Optional provider name.")


class GiftCardSupportedRequest(BaseModel):
    """Request schema for supported gift card listing requests."""

    provider_name: str | None = Field(default=None, description="Optional provider name.")


class GiftCardCountryRequest(BaseModel):
    """Request schema for supported countries and currencies lookups."""

    provider_name: str | None = Field(default=None, description="Optional provider name.")


class GiftCardStatusRequest(BaseModel):
    """Request schema for gift card transaction status lookups."""

    reference: str = Field(..., min_length=1, description="Gift card transaction reference.")


class GiftCardHistoryRequest(BaseModel):
    """Request schema for gift card transaction history requests."""

    reference: str = Field(..., min_length=1, description="Gift card transaction reference.")


class GiftCardDetailRequest(BaseModel):
    """Request schema for gift card transaction detail requests."""

    reference: str = Field(..., min_length=1, description="Gift card transaction reference.")


class GiftCardController:
    """Thin FastAPI controller for gift card endpoints."""

    def __init__(self, giftcard_service: GiftCardService, logger: logging.Logger | None = None) -> None:
        self.giftcard_service = giftcard_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/giftcards", tags=["Gift Cards"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/valuation", status_code=status.HTTP_200_OK)(self.valuate_giftcard)
        self.router.post("/trade", status_code=status.HTTP_201_CREATED)(self.trade_giftcard)
        self.router.post("/pricing", status_code=status.HTTP_200_OK)(self.get_pricing)
        self.router.post("/initiate", status_code=status.HTTP_201_CREATED)(self.initiate_transaction)
        self.router.post("/settle", status_code=status.HTTP_200_OK)(self.settle_transaction)
        self.router.post("/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_transaction)
        self.router.get("/supported", status_code=status.HTTP_200_OK)(self.get_supported_cards)
        self.router.get("/countries", status_code=status.HTTP_200_OK)(self.get_supported_countries)
        self.router.get("/status/{reference}", status_code=status.HTTP_200_OK)(self.get_trade_status)
        self.router.post("/history", status_code=status.HTTP_200_OK)(self.get_trade_history)
        self.router.get("/details/{reference}", status_code=status.HTTP_200_OK)(self.get_trade_details)

    async def valuate_giftcard(self, payload: GiftCardValuationRequest) -> dict[str, Any]:
        """Handle gift card valuation requests."""
        return await self._execute(
            action="valuate_giftcard",
            handler=self.giftcard_service.calculate_valuation,
            payload={
                "amount": payload.amount,
                "brand": payload.brand,
                "card_type": payload.card_type,
                "country": payload.country,
                "currency": payload.currency,
                "provider_name": payload.provider_name,
                "promotion_code": payload.promotion_code,
            },
            success_message="Gift card valuation retrieved successfully.",
        )

    async def trade_giftcard(self, payload: GiftCardTradeRequest) -> dict[str, Any]:
        """Handle gift card trading requests."""
        return await self._execute(
            action="trade_giftcard",
            handler=self.giftcard_service.buy_giftcard,
            payload={
                "user_id": payload.user_id,
                "brand": payload.brand,
                "card_type": payload.card_type,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "country": payload.country,
                "currency": payload.currency,
                "wallet_id": payload.wallet_id,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Gift card trade initiated successfully.",
        )

    async def get_pricing(self, payload: GiftCardPricingRequest) -> dict[str, Any]:
        """Handle gift card pricing requests."""
        return await self._execute(
            action="get_pricing",
            handler=self.giftcard_service.calculate_pricing,
            payload={
                "amount": payload.amount,
                "brand": payload.brand,
                "card_type": payload.card_type,
                "country": payload.country,
                "denomination": payload.denomination,
                "card_format": payload.card_format,
                "currency": payload.currency,
                "provider_name": payload.provider_name,
                "promotion_code": payload.promotion_code,
            },
            success_message="Gift card pricing retrieved successfully.",
        )

    async def initiate_transaction(self, payload: GiftCardInitiationRequest) -> dict[str, Any]:
        """Handle gift card transaction initiation requests."""
        return await self._execute(
            action="initiate_transaction",
            handler=self.giftcard_service.buy_giftcard,
            payload={
                "user_id": payload.user_id,
                "brand": payload.brand,
                "card_type": payload.card_type,
                "amount": payload.amount,
                "transaction_pin": payload.transaction_pin,
                "country": payload.country,
                "currency": payload.currency,
                "wallet_id": payload.wallet_id,
                "description": payload.description,
                "provider_name": payload.provider_name,
                "metadata_payload": payload.metadata_payload,
            },
            success_message="Gift card transaction initiated successfully.",
        )

    async def settle_transaction(self, payload: GiftCardSettlementRequest) -> dict[str, Any]:
        """Handle gift card settlement requests."""
        return await self._execute(
            action="settle_transaction",
            handler=self.giftcard_service.settle_giftcard_transaction,
            payload={
                "reference": payload.reference,
                "provider_name": payload.provider_name,
                "partial_amount": payload.partial_amount,
                "settlement_reference": payload.settlement_reference,
            },
            success_message="Gift card settlement completed successfully.",
        )

    async def reconcile_transaction(self, payload: GiftCardReconciliationRequest) -> dict[str, Any]:
        """Handle gift card reconciliation requests."""
        return await self._execute(
            action="reconcile_transaction",
            handler=self.giftcard_service.reconcile_transaction,
            payload={"reference": payload.reference, "provider_name": payload.provider_name},
            success_message="Gift card transaction reconciled successfully.",
        )

    async def get_supported_cards(self, payload: GiftCardSupportedRequest) -> dict[str, Any]:
        """Handle supported gift card listing requests."""
        return await self._execute(
            action="get_supported_cards",
            handler=self.giftcard_service.calculate_valuation,
            payload={
                "amount": Decimal("0"),
                "brand": payload.provider_name or "",
                "card_type": "",
                "country": None,
                "currency": "NGN",
                "provider_name": payload.provider_name,
                "promotion_code": None,
            },
            success_message="Supported gift cards retrieved successfully.",
        )

    async def get_supported_countries(self, payload: GiftCardCountryRequest) -> dict[str, Any]:
        """Handle supported countries and currencies requests."""
        return await self._execute(
            action="get_supported_countries",
            handler=self.giftcard_service.calculate_valuation,
            payload={
                "amount": Decimal("0"),
                "brand": payload.provider_name or "",
                "card_type": "",
                "country": None,
                "currency": "NGN",
                "provider_name": payload.provider_name,
                "promotion_code": None,
            },
            success_message="Supported countries and currencies retrieved successfully.",
        )

    async def get_trade_status(self, reference: str) -> dict[str, Any]:
        """Handle gift card transaction status requests."""
        return await self._execute(
            action="get_trade_status",
            handler=self.giftcard_service.get_trade_status,
            payload={"reference": reference},
            success_message="Gift card transaction status retrieved successfully.",
        )

    async def get_trade_history(self, payload: GiftCardHistoryRequest) -> dict[str, Any]:
        """Handle gift card transaction history requests."""
        return await self._execute(
            action="get_trade_history",
            handler=self.giftcard_service.get_trade_status,
            payload={"reference": payload.reference},
            success_message="Gift card transaction history retrieved successfully.",
        )

    async def get_trade_details(self, reference: str) -> dict[str, Any]:
        """Handle gift card transaction detail requests."""
        return await self._execute(
            action="get_trade_details",
            handler=self.giftcard_service.get_trade_details,
            payload={"reference": reference},
            success_message="Gift card transaction details retrieved successfully.",
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

        log_api_event(self.logger, "giftcard_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "giftcard_request_failed", action=action, error=str(exc))
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
