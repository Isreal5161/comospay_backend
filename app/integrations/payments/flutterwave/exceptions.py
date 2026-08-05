from __future__ import annotations

from typing import Any


class FlutterwaveError(Exception):
    """Base exception for Flutterwave integration failures."""

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        payload: Any = None,
    ) -> None:
        self.message = message or "Flutterwave request failed"
        self.code = code
        self.payload = payload
        super().__init__(self.message)


class FlutterwaveAuthenticationError(FlutterwaveError):
    """Raised when Flutterwave authentication fails."""


class FlutterwaveValidationError(FlutterwaveError):
    """Raised when a Flutterwave request is invalid."""


class FlutterwaveBadRequestError(FlutterwaveError):
    """Raised when Flutterwave rejects a request as malformed."""


class FlutterwaveUnauthorizedError(FlutterwaveError):
    """Raised when Flutterwave rejects the request due to missing or invalid credentials."""


class FlutterwaveForbiddenError(FlutterwaveError):
    """Raised when Flutterwave denies access to a requested resource."""


class FlutterwaveNotFoundError(FlutterwaveError):
    """Raised when Flutterwave cannot find the requested resource."""


class FlutterwaveConflictError(FlutterwaveError):
    """Raised when Flutterwave reports a conflict with the current state."""


class FlutterwaveRateLimitError(FlutterwaveError):
    """Raised when Flutterwave rate limiting is encountered."""


class FlutterwaveServerError(FlutterwaveError):
    """Raised when Flutterwave returns a server-side error."""


class FlutterwaveTimeoutError(FlutterwaveError):
    """Raised when a Flutterwave request exceeds the allowed time."""


class FlutterwaveConnectionError(FlutterwaveError):
    """Raised when a Flutterwave request cannot be completed due to connectivity issues."""


class FlutterwaveAPIError(FlutterwaveError):
    """Raised when Flutterwave returns an API error response."""


class FlutterwaveWebhookVerificationError(FlutterwaveError):
    """Raised when a Flutterwave webhook signature cannot be verified."""
