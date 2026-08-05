from __future__ import annotations

import logging
import re
import time
from typing import Any
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
from app.integrations.payments.flutterwave.models import AccountResolutionResponse, BankListResponse, AccountResolutionRequest
from app.integrations.payments.flutterwave.utils import sanitize_payload


class FlutterwaveBankService:
    """Thin integration service for Flutterwave bank-related endpoints."""

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

    async def list_banks(self, *, country: str = "NG", request_id: str | None = None) -> BankListResponse:
        """Retrieve a typed list of supported banks for the given country."""
        normalized_country = self._validate_country(country)
        resolved_request_id = request_id or f"banks-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        try:
            payload = await self.client.get(
                f"/banks/{normalized_country}",
                params={"country": normalized_country},
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/banks") from exc

        self._log_completion("list_banks", resolved_request_id, started_at, 200)
        return BankListResponse.model_validate(payload or {})

    async def resolve_account_number(
        self,
        *,
        account_number: str,
        bank_code: str,
        request_id: str | None = None,
    ) -> AccountResolutionResponse:
        """Validate a bank account number using Flutterwave account resolution."""
        normalized_account = self._validate_account_number(account_number)
        normalized_bank_code = self._validate_bank_code(bank_code)
        resolved_request_id = request_id or f"account-resolution-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        request_payload = AccountResolutionRequest(
            account_number=normalized_account,
            account_bank=normalized_bank_code,
        )

        try:
            payload = await self.client.post(
                "/accounts/resolve",
                json=sanitize_payload(request_payload.model_dump()),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/accounts/resolve") from exc

        self._log_completion("resolve_account_number", resolved_request_id, started_at, 200)
        return AccountResolutionResponse.model_validate(payload or {})

    def _build_headers(self, request_id: str) -> dict[str, str]:
        """Create request headers for the bank operations."""
        headers = self.authentication.get_headers()
        headers["X-Request-ID"] = request_id
        return headers

    def _validate_country(self, country: str | None) -> str:
        """Validate and normalize the country code used for bank lookups."""
        if country is None:
            return "NG"
        normalized = country.strip().upper()
        if not normalized:
            raise FlutterwaveValidationError("Country code is required.")
        return normalized

    def _validate_account_number(self, account_number: str | None) -> str:
        """Validate that the account number is a non-empty numeric string."""
        if account_number is None:
            raise FlutterwaveValidationError("Account number is required.")
        normalized = account_number.strip()
        if not normalized or not re.fullmatch(r"\d+", normalized):
            raise FlutterwaveValidationError("Account number must be numeric.")
        return normalized

    def _validate_bank_code(self, bank_code: str | None) -> str:
        """Validate that the bank code is a non-empty string."""
        if bank_code is None:
            raise FlutterwaveValidationError("Bank code is required.")
        normalized = bank_code.strip()
        if not normalized:
            raise FlutterwaveValidationError("Bank code is required.")
        return normalized

    def _map_exception(self, exc: Exception, request_id: str, endpoint: str) -> Exception:
        """Map provider exceptions into the integration-specific exception hierarchy."""
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
        """Log the completed request metadata without exposing sensitive data."""
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        self.logger.info(
            "flutterwave_bank_operation",
            extra={
                "event": "flutterwave_bank_operation",
                "action": action,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "status_code": status_code,
            },
        )
