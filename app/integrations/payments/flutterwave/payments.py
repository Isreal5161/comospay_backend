from __future__ import annotations

import logging
import time
from typing import Any, Mapping
from uuid import uuid4

from app.integrations.payments.flutterwave.authentication import FlutterwaveAuthentication
from app.integrations.payments.flutterwave.client import FlutterwaveAPIError, FlutterwaveClient, FlutterwaveRequestError
from app.integrations.payments.flutterwave.exceptions import (
    FlutterwaveAPIError as FlutterwaveIntegrationAPIError,
    FlutterwaveBadRequestError,
    FlutterwaveConflictError,
    FlutterwaveConnectionError,
    FlutterwaveForbiddenError,
    FlutterwaveNotFoundError,
    FlutterwaveRateLimitError,
    FlutterwaveServerError,
    FlutterwaveTimeoutError,
    FlutterwaveUnauthorizedError,
    FlutterwaveValidationError,
)
from app.integrations.payments.flutterwave.models import PaymentInitializationRequest, PaymentInitializationResponse
from app.integrations.payments.flutterwave.utils import build_idempotency_key, format_metadata, remove_none_values, sanitize_payload


class FlutterwavePaymentService:
    """Thin integration service for Flutterwave payment collection endpoints."""

    def __init__(
        self,
        client: FlutterwaveClient,
        *,
        authentication: FlutterwaveAuthentication | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.authentication = authentication or FlutterwaveAuthentication()
        self.logger = logger or logging.getLogger(__name__)

    async def initialize_payment(
        self,
        *,
        tx_ref: str,
        amount: float | int,
        currency: str = "NGN",
        redirect_url: str | None = None,
        customer: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> PaymentInitializationResponse:
        """Initialize a payment through Flutterwave's payment collection endpoint."""
        self._validate_tx_ref(tx_ref)
        self._validate_amount(amount)
        normalized_currency = self._validate_currency(currency)
        resolved_request_id = request_id or f"payment-init-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id, tx_ref=tx_ref)

        request_payload = PaymentInitializationRequest(
            tx_ref=tx_ref,
            amount=float(amount),
            currency=normalized_currency,
            redirect_url=redirect_url,
            customer=dict(customer or {}),
            meta=format_metadata(metadata),
        )

        try:
            payload = await self.client.post(
                "/payments",
                json=sanitize_payload(remove_none_values(request_payload.model_dump())),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/payments") from exc

        self._log_completion("initialize_payment", resolved_request_id, started_at, 200)
        return PaymentInitializationResponse.model_validate(payload or {})

    async def verify_transaction(
        self,
        *,
        transaction_id: str | None = None,
        tx_ref: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify a payment transaction using Flutterwave's verification endpoint."""
        if not transaction_id and not tx_ref:
            raise FlutterwaveValidationError("Transaction identifier or transaction reference is required.")

        resolved_request_id = request_id or f"payment-verify-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)
        identifier = transaction_id or tx_ref

        try:
            payload = await self.client.get(
                "/transactions/{identifier}/verify".format(identifier=identifier),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transactions/verify") from exc

        self._log_completion("verify_transaction", resolved_request_id, started_at, 200)
        return payload or {}

    async def get_transaction(
        self,
        *,
        transaction_id: str | None = None,
        tx_ref: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve a payment transaction by identifier or reference."""
        if not transaction_id and not tx_ref:
            raise FlutterwaveValidationError("Transaction identifier or transaction reference is required.")

        resolved_request_id = request_id or f"payment-get-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)
        identifier = transaction_id or tx_ref

        try:
            payload = await self.client.get(
                "/transactions/{identifier}".format(identifier=identifier),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transactions") from exc

        self._log_completion("get_transaction", resolved_request_id, started_at, 200)
        return payload or {}

    def _build_headers(self, request_id: str, *, tx_ref: str | None = None) -> dict[str, str]:
        """Build headers for payment requests without exposing sensitive values."""
        headers = self.authentication.get_headers()
        headers["X-Request-ID"] = request_id
        headers["Idempotency-Key"] = build_idempotency_key("payment-init", tx_ref or request_id)
        return headers

    def _validate_tx_ref(self, tx_ref: str | None) -> None:
        """Validate the transaction reference before sending it to Flutterwave."""
        if not tx_ref or not str(tx_ref).strip():
            raise FlutterwaveValidationError("Transaction reference is required.")

    def _validate_amount(self, amount: float | int) -> None:
        """Validate the payment amount."""
        if amount is None:
            raise FlutterwaveValidationError("Amount is required.")
        if not isinstance(amount, (int, float)):
            raise FlutterwaveValidationError("Amount must be numeric.")
        if float(amount) <= 0:
            raise FlutterwaveValidationError("Amount must be greater than zero.")

    def _validate_currency(self, currency: str | None) -> str:
        """Validate and normalize the payment currency."""
        if not currency or not str(currency).strip():
            return "NGN"
        normalized = str(currency).strip().upper()
        if not normalized:
            return "NGN"
        return normalized

    def _map_exception(self, exc: Exception, request_id: str, endpoint: str) -> Exception:
        """Map provider-level failures to the integration exception hierarchy."""
        if isinstance(exc, FlutterwaveValidationError):
            return exc
        if isinstance(exc, FlutterwaveIntegrationAPIError):
            message = str(exc)
            if "400" in message:
                return FlutterwaveBadRequestError(message, code="400")
            if "401" in message:
                return FlutterwaveUnauthorizedError(message, code="401")
            if "403" in message:
                return FlutterwaveForbiddenError(message, code="403")
            if "404" in message:
                return FlutterwaveNotFoundError(message, code="404")
            if "409" in message:
                return FlutterwaveConflictError(message, code="409")
            if "422" in message:
                return FlutterwaveBadRequestError(message, code="422")
            if "429" in message:
                return FlutterwaveRateLimitError(message, code="429")
            if "500" in message:
                return FlutterwaveServerError(message, code="500")
            return FlutterwaveIntegrationAPIError(message)
        if isinstance(exc, FlutterwaveAPIError):
            return FlutterwaveIntegrationAPIError(str(exc))
        if isinstance(exc, FlutterwaveRequestError):
            message = str(exc)
            if "timed out" in message.lower():
                return FlutterwaveTimeoutError(message, code="timeout")
            return FlutterwaveConnectionError(message, code="connection")
        if isinstance(exc, TimeoutError):
            return FlutterwaveTimeoutError(str(exc), code="timeout")
        if isinstance(exc, ConnectionError):
            return FlutterwaveConnectionError(str(exc), code="connection")
        return FlutterwaveIntegrationAPIError(str(exc))

    def _log_completion(self, action: str, request_id: str, started_at: float, status_code: int) -> None:
        """Log payment request completion details without exposing sensitive values."""
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        self.logger.info(
            "flutterwave_payment_operation",
            extra={
                "event": "flutterwave_payment_operation",
                "action": action,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "status_code": status_code,
            },
        )
