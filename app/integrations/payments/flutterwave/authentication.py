from __future__ import annotations

from typing import Any

from app.config.settings import settings


class FlutterwaveAuthenticationError(Exception):
    """Raised when Flutterwave authentication configuration is invalid."""


class FlutterwaveAuthentication:
    """Builds authentication headers for Flutterwave API requests."""

    def __init__(self, secret_key: str | None = None) -> None:
        self._secret_key = self._resolve_secret_key(secret_key)

    def get_headers(self, *, api_version: str | None = None) -> dict[str, str]:
        """Return the headers required for Flutterwave API requests."""
        if not self._secret_key:
            raise FlutterwaveAuthenticationError("Flutterwave secret key is not configured.")

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._secret_key}",
        }
        if api_version:
            headers["X-API-Version"] = api_version
        return headers

    def get_auth_header(self) -> str:
        """Return the authorization header value."""
        return f"Bearer {self._secret_key}"

    def _resolve_secret_key(self, secret_key: str | None) -> str:
        """Resolve the configured Flutterwave secret key from explicit input or settings."""
        if secret_key:
            return secret_key

        configured = getattr(settings, "flutterwave_secret_key", None)
        if configured is None:
            return ""
        if hasattr(configured, "get_secret_value"):
            return configured.get_secret_value()
        return str(configured)
