from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from app.config.settings import settings


class FlutterwaveError(Exception):
    """Base exception for Flutterwave client failures."""


class FlutterwaveConfigurationError(FlutterwaveError):
    """Raised when required Flutterwave configuration is missing."""


class FlutterwaveRequestError(FlutterwaveError):
    """Raised when a request to Flutterwave fails."""


class FlutterwaveAPIError(FlutterwaveRequestError):
    """Raised when Flutterwave returns an error response."""


class FlutterwaveClient:
    """Reusable async HTTP client for Flutterwave API interactions."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = self._resolve_base_url(base_url)
        self.api_key = self._resolve_api_key(api_key)
        self.timeout = self._resolve_timeout(timeout)
        self._headers = dict(headers or {})
        self._client: httpx.AsyncClient | None = None
        self._logger = logging.getLogger(__name__)

    async def __aenter__(self) -> "FlutterwaveClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying AsyncClient if it is active."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

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

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform an authenticated POST request."""
        return await self._request(
            "POST",
            path,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def put(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform an authenticated PUT request."""
        return await self._request(
            "PUT",
            path,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def patch(
        self,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform an authenticated PATCH request."""
        return await self._request(
            "PATCH",
            path,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform an authenticated DELETE request."""
        return await self._request(
            "DELETE",
            path,
            params=params,
            headers=headers,
            timeout=timeout,
            request_id=request_id,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Perform a single HTTP request and parse the JSON response."""
        client = await self._ensure_client()
        request_headers = self._build_headers(headers, request_id=request_id)

        self._logger.info(
            "flutterwave_request",
            extra={
                "event": "flutterwave_request",
                "method": method.upper(),
                "path": path,
                "request_id": request_id,
            },
        )

        try:
            response = await client.request(
                method.upper(),
                path,
                params=params,
                json=json,
                headers=request_headers,
                timeout=timeout or self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise FlutterwaveRequestError(f"Flutterwave request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise FlutterwaveRequestError(f"Flutterwave request failed: {exc}") from exc

        self._logger.info(
            "flutterwave_response",
            extra={
                "event": "flutterwave_response",
                "method": method.upper(),
                "path": path,
                "status_code": response.status_code,
                "request_id": request_id,
            },
        )

        if response.status_code >= 400:
            raise FlutterwaveAPIError(
                f"Flutterwave API error {response.status_code}: {response.text}"
            )

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise FlutterwaveRequestError("Flutterwave returned a non-JSON response") from exc

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Create the underlying AsyncClient lazily."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self, headers: Mapping[str, str] | None = None, *, request_id: str | None = None) -> dict[str, str]:
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
            return base_url.rstrip("/")

        configured = getattr(settings, "flutterwave_base_url", None)
        if configured:
            return str(configured).rstrip("/")

        configured = getattr(settings, "flutterwave_api_url", None)
        if configured:
            return str(configured).rstrip("/")

        return "https://api.flutterwave.com/v3"

    def _resolve_api_key(self, api_key: str | None) -> str:
        """Resolve the Flutterwave secret key from explicit input or settings."""
        if api_key:
            return api_key

        secret = getattr(settings, "flutterwave_secret_key", None)
        if isinstance(secret, str):
            return secret
        if secret is not None:
            return str(secret.get_secret_value()) if hasattr(secret, "get_secret_value") else str(secret)

        return ""

    def _resolve_timeout(self, timeout: float | None) -> httpx.Timeout:
        """Resolve the request timeout from explicit input or settings."""
        if timeout is not None:
            return httpx.Timeout(float(timeout))

        connect_timeout = getattr(settings, "connection_timeout", None)
        read_timeout = getattr(settings, "read_timeout", None)
        write_timeout = getattr(settings, "write_timeout", None)

        if connect_timeout is None and read_timeout is None and write_timeout is None:
            return httpx.Timeout(10.0)

        return httpx.Timeout(
            connect=float(connect_timeout) if connect_timeout is not None else None,
            read=float(read_timeout) if read_timeout is not None else None,
            write=float(write_timeout) if write_timeout is not None else None,
            pool=None,
        )
