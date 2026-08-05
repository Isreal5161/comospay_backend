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
from app.integrations.payments.flutterwave.models import VirtualAccountRequest, VirtualAccountResponse
from app.integrations.payments.flutterwave.utils import build_idempotency_key, remove_none_values, sanitize_payload


class FlutterwaveVirtualAccountService:
    """Thin integration service for Flutterwave dedicated virtual accounts."""

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

    async def create_virtual_account(
        self,
        *,
        customer: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> VirtualAccountResponse:
        """Create a dedicated virtual account for a customer."""
        if not customer:
            raise FlutterwaveValidationError("Customer details are required.")

        resolved_request_id = request_id or f"virtual-account-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        request_payload = VirtualAccountRequest(
            email=str(customer.get("email") or "") if customer.get("email") else None,
            phonenumber=str(customer.get("phone_number") or "") if customer.get("phone_number") else None,
            firstname=str(customer.get("first_name") or "") if customer.get("first_name") else None,
            lastname=str(customer.get("last_name") or "") if customer.get("last_name") else None,
            nickname=str(customer.get("nickname") or "") if customer.get("nickname") else None,
            meta=dict(metadata or {}),
        )

        if not any(
            value is not None and str(value).strip() for value in [request_payload.email, request_payload.phonenumber]
        ):
            raise FlutterwaveValidationError("Customer email or phone number is required.")

        try:
            payload = await self.client.post(
                "/virtual-account-numbers",
                json=sanitize_payload(remove_none_values(request_payload.model_dump())),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/virtual-account-numbers") from exc

        self._log_completion("create_virtual_account", resolved_request_id, started_at, 200)
        return VirtualAccountResponse.model_validate(payload or {})

    async def get_virtual_account(
        self,
        *,
        identifier: str | None = None,
        reference: str | None = None,
        request_id: str | None = None,
    ) -> VirtualAccountResponse:
        """Retrieve a dedicated virtual account by identifier or reference."""
        if not identifier and not reference:
            raise FlutterwaveValidationError("Identifier or reference is required.")

        resolved_request_id = request_id or f"virtual-account-get-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)
        lookup_value = identifier or reference

        try:
            payload = await self.client.get(
                "/virtual-account-numbers/{identifier}".format(identifier=lookup_value),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/virtual-account-numbers") from exc

        self._log_completion("get_virtual_account", resolved_request_id, started_at, 200)
        return VirtualAccountResponse.model_validate(payload or {})

    async def list_virtual_accounts(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        filters: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> VirtualAccountResponse:
        """List dedicated virtual accounts with optional pagination and filters."""
        resolved_request_id = request_id or f"virtual-account-list-{uuid4().hex}"
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
                "/virtual-account-numbers",
                params=params,
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/virtual-account-numbers") from exc

        self._log_completion("list_virtual_accounts", resolved_request_id, started_at, 200)
        return VirtualAccountResponse.model_validate(payload or {})

    async def update_virtual_account(
        self,
        *,
        identifier: str,
        updates: Mapping[str, Any],
        request_id: str | None = None,
    ) -> VirtualAccountResponse:
        """Update supported dedicated virtual account details when the provider permits it."""
        if not identifier:
            raise FlutterwaveValidationError("Identifier is required.")
        if not updates:
            raise FlutterwaveValidationError("Updates are required.")

        resolved_request_id = request_id or f"virtual-account-update-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)
        payload = sanitize_payload(remove_none_values(dict(updates)))

        try:
            response = await self.client.put(
                "/virtual-account-numbers/{identifier}".format(identifier=identifier),
                json=payload,
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/virtual-account-numbers") from exc

        self._log_completion("update_virtual_account", resolved_request_id, started_at, 200)
        return VirtualAccountResponse.model_validate(response or {})

    async def deactivate_virtual_account(
        self,
        *,
        identifier: str,
        request_id: str | None = None,
    ) -> VirtualAccountResponse:
        """Deactivate a dedicated virtual account when supported by Flutterwave."""
        if not identifier:
            raise FlutterwaveValidationError("Identifier is required.")

        resolved_request_id = request_id or f"virtual-account-deactivate-{uuid4().hex}"
        started_at = time.perf_counter()
        headers = self._build_headers(resolved_request_id)

        try:
            payload = await self.client.delete(
                "/virtual-account-numbers/{identifier}".format(identifier=identifier),
                headers=headers,
                request_id=resolved_request_id,
            )
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise self._map_exception(exc, resolved_request_id, "/virtual-account-numbers") from exc

        self._log_completion("deactivate_virtual_account", resolved_request_id, started_at, 200)
        return VirtualAccountResponse.model_validate(payload or {})

    def _build_headers(self, request_id: str) -> dict[str, str]:
        """Build headers for Flutterwave virtual account requests."""
        headers = self.authentication.get_headers()
        headers["X-Request-ID"] = request_id
        return headers

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
        """Log integration execution details without exposing sensitive data."""
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        self.logger.info(
            "flutterwave_virtual_account_operation",
            extra={
                "event": "flutterwave_virtual_account_operation",
                "action": action,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "status_code": status_code,
            },
        )
