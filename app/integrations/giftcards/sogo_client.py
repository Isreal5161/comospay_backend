from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx
from pydantic import SecretStr

from app.config.settings import settings
from app.schemas.giftcard_schema import GiftCardSellSubmission


class SogoError(Exception):
    """Base exception for Sogo client failures."""


class SogoConfigurationError(SogoError):
    """Raised when required Sogo configuration is missing."""


class SogoRequestError(SogoError):
    """Raised when a request to Sogo fails."""


class SogoAPIError(SogoRequestError):
    """Raised when Sogo returns an error response."""


class SogoClient:
    """Reusable async HTTP client for Sogo Gift Card API interactions."""

    LIVE_BASE_URL = "https://api.sogo.africa/v1"
    SANDBOX_BASE_URL = "https://sandbox.sogo.africa/v1"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | SecretStr | None = None,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = self._resolve_base_url(base_url)
        self.api_key = self._resolve_api_key(api_key)
        self.timeout = self._resolve_timeout(timeout)
        self._headers = dict(headers or {})
        self._client: httpx.AsyncClient | None = None
        self._logger = logging.getLogger(__name__)

    async def __aenter__(self) -> "SogoClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying AsyncClient if it is active."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_catalog(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Fetch the complete gift card catalog."""
        return await self._request(
            "GET",
            "/gift-cards/sell/catalog",
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def get_rates(
        self,
        *,
        slug: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Fetch gift card sell rates, optionally filtered by brand slug."""
        params = {}
        if slug:
            params["slug"] = slug
        return await self._request(
            "GET",
            "/gift-cards/sell/rates",
            params=params or None,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform an authenticated GET request."""
        return await self._request(
            "GET",
            path,
            params=params,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def sell_gift_card(
        self,
        submission: GiftCardSellSubmission,
        *,
        timeout: float | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Submit a validated gift-card sell request as multipart form data.

        The idempotency_key is sent as the Idempotency-Key header to Sogo for
        request deduplication. Use a stable value like the transaction reference.
        """
        if submission.additional_info is None:
            raise SogoRequestError("Sogo sell submission requires additional information.")
        data: dict[str, str] = {
            "slug": submission.brand_slug,
            "card_country": submission.card_country,
            "card_type": submission.card_type,
            "card_currency": submission.card_currency,
            "card_amount": str(submission.card_amount),
            "payout_currency": submission.payout_currency,
        }
        if submission.additional_info is not None:
            data["additional_info"] = submission.additional_info
        if submission.sub_type is not None:
            data["sub_type"] = submission.sub_type
        if submission.specific_country is not None:
            data["specific_country"] = submission.specific_country

        files: list[tuple[str, tuple[str, Any, str]]] = []
        for image in submission.images or []:
            filename = getattr(image, "filename", None) or "image"
            content_type = getattr(image, "content_type", None)
            file_object = getattr(image, "file", None)
            if file_object is None:
                file_object = getattr(image, "content", None)
            if file_object is None or content_type is None:
                raise SogoRequestError("Sogo image data is invalid.")
            files.append(("images[]", (filename, file_object, content_type)))

        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        return await self._request(
            "POST",
            "/gift-cards/sell",
            data=data,
            files=files,
            timeout=timeout,
            request_id=request_id,
            headers=headers if headers else None,
            multipart=True,
            expected_status=201,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
        multipart: bool = False,
        expected_status: int | None = None,
    ) -> Any:
        """Perform a single HTTP request and parse the JSON response."""
        client = await self._ensure_client()
        request_headers = self._build_headers(headers, request_id=request_id)
        if multipart:
            request_headers.pop("Content-Type", None)

        self._logger.info(
            "sogo_request",
            extra={
                "event": "sogo_request",
                "method": method.upper(),
                "path": path,
                "request_id": request_id,
            },
        )

        try:
            request_kwargs: dict[str, Any] = {
                "params": params,
                "headers": request_headers,
                "timeout": timeout or self.timeout,
            }
            if data is not None:
                request_kwargs["data"] = data
            if files is not None:
                request_kwargs["files"] = files
            response = await client.request(method.upper(), path, **request_kwargs)
        except httpx.TimeoutException as exc:
            self._logger.warning(
                "sogo_request_timeout",
                extra={"path": path, "request_id": request_id, "error": str(exc)},
            )
            raise SogoRequestError(f"Sogo request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            self._logger.warning(
                "sogo_connection_error",
                extra={"path": path, "request_id": request_id, "error": str(exc)},
            )
            raise SogoRequestError(f"Sogo request failed: {exc}") from exc

        self._logger.info(
            "sogo_response",
            extra={
                "event": "sogo_response",
                "method": method.upper(),
                "path": path,
                "status_code": response.status_code,
                "request_id": request_id,
            },
        )

        if response.status_code >= 400:
            self._logger.warning(
                "sogo_api_error",
                extra={
                    "path": path,
                    "status_code": response.status_code,
                    "request_id": request_id,
                },
            )
            raise SogoAPIError(f"Sogo API request failed with status {response.status_code}.")

        if expected_status is not None and response.status_code != expected_status:
            raise SogoRequestError("Sogo returned an unexpected response status.")

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            self._logger.warning(
                "sogo_malformed_response",
                extra={"path": path, "request_id": request_id, "error": str(exc)},
            )
            raise SogoRequestError("Sogo returned a non-JSON response") from exc

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create the underlying AsyncClient lazily."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(
        self, headers: Mapping[str, str] | None = None, *, request_id: str | None = None
    ) -> dict[str, str]:
        """Build headers for requests, including authentication and trace metadata."""
        merged_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        merged_headers.update(self._headers)
        if headers:
            merged_headers.update(headers)
        if request_id:
            merged_headers["X-Request-ID"] = request_id
        if self.api_key:
            merged_headers["Authorization"] = f"Bearer {self.api_key}"
        return merged_headers

    def _resolve_base_url(self, base_url: str | None) -> str:
        """Resolve the API base URL from explicit input or settings."""
        if base_url:
            resolved = base_url.rstrip("/")
        else:
            configured = getattr(settings, "sogo_api_base_url", None)
            if configured:
                resolved = str(configured).rstrip("/")
            else:
                resolved = self.SANDBOX_BASE_URL

        if resolved not in {self.LIVE_BASE_URL, self.SANDBOX_BASE_URL}:
            raise SogoConfigurationError("Sogo API base URL must be the documented live or sandbox URL.")
        return resolved

    def _resolve_api_key(self, api_key: str | SecretStr | None) -> str:
        """Resolve the Sogo API key from explicit input or settings."""
        if api_key:
            resolved = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        else:
            configured = getattr(settings, "sogo_api_key", None)
            if not configured:
                raise SogoConfigurationError("Sogo API key is not configured.")
            resolved = configured.get_secret_value() if isinstance(configured, SecretStr) else str(configured)

        if not resolved:
            raise SogoConfigurationError("Sogo API key is not configured.")
        self._validate_environment_key(resolved)
        return resolved

    def _validate_environment_key(self, api_key: str) -> None:
        """Reject credentials issued for the other documented Sogo environment."""
        if self.base_url == self.SANDBOX_BASE_URL and not api_key.startswith("sogo_sk_test_"):
            raise SogoConfigurationError("Sandbox Sogo URL requires a sandbox secret key.")
        if self.base_url == self.LIVE_BASE_URL and not api_key.startswith("sogo_sk_live_"):
            raise SogoConfigurationError("Live Sogo URL requires a live secret key.")

    def _resolve_timeout(self, timeout: float | None) -> float:
        """Resolve the request timeout from explicit input or settings."""
        if timeout is not None:
            return float(timeout)

        configured = getattr(settings, "sogo_timeout_seconds", None)
        if configured is not None:
            return float(configured)

        return 10.0
