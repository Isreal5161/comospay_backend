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
from app.integrations.payments.flutterwave.models import TransferRequest, TransferResponse
from app.integrations.payments.flutterwave.utils import build_idempotency_key, format_metadata, remove_none_values, sanitize_payload


class FlutterwaveTransferService:
    """Thin integration service for Flutterwave transfer endpoints."""

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

    async def create_transfer(
        self,
        *,
        account_bank: str,
        account_number: str,
        amount: float | int,
        narration: str | None = None,
        reference: str | None = None,
        currency: str = "NGN",
        request_id: str | None = None,
    ) -> TransferResponse:
        """Create a transfer through Flutterwave's transfer endpoint."""
        self._validate_account_bank(account_bank)
        self._validate_account_number(account_number)
        self._validate_amount(amount)
        normalized_currency = self._validate_currency(currency)
        resolved_request_id = request_id or f"transfer-create-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        request_payload = TransferRequest(
            account_bank=account_bank.strip(),
            account_number=account_number.strip(),
            amount=float(amount),
            narration=narration,
            reference=reference,
            currency=normalized_currency,
        )

        try:
            payload = await self.client.post(
                "/transfers",
                json=sanitize_payload(remove_none_values(request_payload.model_dump())),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transfers") from exc

        self._log_completion("create_transfer", resolved_request_id, started_at, 200)
        return TransferResponse.model_validate(payload or {})

    async def get_transfer_details(
        self,
        *,
        transfer_id: str | None = None,
        reference: str | None = None,
        request_id: str | None = None,
    ) -> TransferResponse:
        """Retrieve transfer details using the transfer identifier or reference."""
        if not transfer_id and not reference:
            raise FlutterwaveValidationError("Transfer identifier or reference is required.")

        resolved_request_id = request_id or f"transfer-get-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)
        identifier = transfer_id or reference

        try:
            payload = await self.client.get(
                "/transfers/{identifier}".format(identifier=identifier),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transfers") from exc

        self._log_completion("get_transfer_details", resolved_request_id, started_at, 200)
        return TransferResponse.model_validate(payload or {})

    async def list_transfers(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        filters: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> TransferResponse:
        """List transfers with optional pagination and filters."""
        resolved_request_id = request_id or f"transfer-list-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size
        if filters:
            params.update(dict(filters))

        try:
            payload = await self.client.get(
                "/transfers",
                params=params,
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transfers") from exc

        self._log_completion("list_transfers", resolved_request_id, started_at, 200)
        return TransferResponse.model_validate(payload or {})

    async def retry_transfer(
        self,
        *,
        transfer_id: str,
        request_id: str | None = None,
    ) -> TransferResponse:
        """Retry a transfer when the provider exposes this operation."""
        if not transfer_id:
            raise FlutterwaveValidationError("Transfer identifier is required.")

        resolved_request_id = request_id or f"transfer-retry-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        try:
            payload = await self.client.post(
                "/transfers/{transfer_id}/retries".format(transfer_id=transfer_id),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transfers") from exc

        self._log_completion("retry_transfer", resolved_request_id, started_at, 200)
        return TransferResponse.model_validate(payload or {})

    async def get_transfer_fee(
        self,
        *,
        account_bank: str,
        account_number: str,
        amount: float | int,
        request_id: str | None = None,
    ) -> TransferResponse:
        """Retrieve the transfer fee for a proposed transfer when supported by Flutterwave."""
        self._validate_account_bank(account_bank)
        self._validate_account_number(account_number)
        self._validate_amount(amount)
        resolved_request_id = request_id or f"transfer-fee-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        params = {
            "account_bank": account_bank.strip(),
            "account_number": account_number.strip(),
            "amount": float(amount),
        }

        try:
            payload = await self.client.get(
                "/transfers/fee",
                params=params,
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/transfers/fee") from exc

        self._log_completion("get_transfer_fee", resolved_request_id, started_at, 200)
        return TransferResponse.model_validate(payload or {})

    def _build_headers(self, request_id: str) -> dict[str, str]:
        """Build headers for transfer requests without exposing sensitive values."""
        headers = self.authentication.get_headers()
        headers["X-Request-ID"] = request_id
        headers["Idempotency-Key"] = build_idempotency_key(request_id)
        return headers

    def _validate_account_bank(self, account_bank: str | None) -> None:
        """Validate the destination bank code."""
        if not account_bank or not str(account_bank).strip():
            raise FlutterwaveValidationError("Destination bank code is required.")

    def _validate_account_number(self, account_number: str | None) -> None:
        """Validate the destination account number."""
        if not account_number or not str(account_number).strip():
            raise FlutterwaveValidationError("Destination account number is required.")

    def _validate_amount(self, amount: float | int) -> None:
        """Validate the transfer amount."""
        if amount is None:
            raise FlutterwaveValidationError("Amount is required.")
        if not isinstance(amount, (int, float)):
            raise FlutterwaveValidationError("Amount must be numeric.")
        if float(amount) <= 0:
            raise FlutterwaveValidationError("Amount must be greater than zero.")

    def _validate_currency(self, currency: str | None) -> str:
        """Validate and normalize the transfer currency."""
        if not currency or not str(currency).strip():
            return "NGN"
        return str(currency).strip().upper()

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
        """Log transfer execution details without exposing sensitive values."""
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        self.logger.info(
            "flutterwave_transfer_operation",
            extra={
                "event": "flutterwave_transfer_operation",
                "action": action,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "status_code": status_code,
            },
        )
