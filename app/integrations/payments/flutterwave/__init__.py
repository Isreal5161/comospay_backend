"""Public package interface for the Flutterwave integration layer."""

from app.integrations.payments.flutterwave.client import FlutterwaveClient


class FlutterwaveAuthentication:
    """Placeholder authentication abstraction for Flutterwave integrations."""


class FlutterwaveError(Exception):
    """Base exception for Flutterwave integration errors."""


class FlutterwaveConfigurationError(FlutterwaveError):
    """Raised when required Flutterwave configuration is missing or invalid."""


class FlutterwaveRequestError(FlutterwaveError):
    """Raised when a Flutterwave request fails."""


class FlutterwaveAPIError(FlutterwaveRequestError):
    """Raised when Flutterwave returns an error response."""


class FlutterwaveAuthenticationError(FlutterwaveRequestError):
    """Raised when Flutterwave authentication fails."""


class FlutterwaveValidationError(FlutterwaveRequestError):
    """Raised when request validation fails."""


class FlutterwaveRateLimitError(FlutterwaveRequestError):
    """Raised when Flutterwave rate limiting is encountered."""


class FlutterwaveNotFoundError(FlutterwaveRequestError):
    """Raised when a requested Flutterwave resource cannot be found."""


class FlutterwaveServerError(FlutterwaveRequestError):
    """Raised when Flutterwave returns a server-side error."""


__all__ = [
    "FlutterwaveAuthentication",
    "FlutterwaveClient",
    "FlutterwaveAPIError",
    "FlutterwaveAuthenticationError",
    "FlutterwaveConfigurationError",
    "FlutterwaveError",
    "FlutterwaveNotFoundError",
    "FlutterwaveRateLimitError",
    "FlutterwaveRequestError",
    "FlutterwaveServerError",
    "FlutterwaveValidationError",
]
