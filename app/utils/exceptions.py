from __future__ import annotations

from fastapi import HTTPException, status


class AppException(HTTPException):
    """Base exception for application errors handled by FastAPI."""

    def __init__(self, status_code: int, detail: str, error_code: str | None = None) -> None:
        self.error_code = error_code or self.__class__.__name__.upper()
        super().__init__(status_code=status_code, detail=detail)


class AuthenticationException(AppException):
    """Raised when user authentication fails."""

    def __init__(self, detail: str = "Authentication failed.", error_code: str = "AUTHENTICATION_FAILED") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, error_code=error_code)


class AuthorizationException(AppException):
    """Raised when a user lacks permissions."""

    def __init__(self, detail: str = "You are not authorized to perform this action.", error_code: str = "AUTHORIZATION_FAILED") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail, error_code=error_code)


class ValidationException(AppException):
    """Raised when request data is invalid."""

    def __init__(self, detail: str = "Validation failed.", error_code: str = "VALIDATION_FAILED") -> None:
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail, error_code=error_code)


class DatabaseException(AppException):
    """Raised when a database operation fails."""

    def __init__(self, detail: str = "A database error occurred.", error_code: str = "DATABASE_ERROR") -> None:
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail, error_code=error_code)


class PaymentException(AppException):
    """Raised for payment-related failures."""

    def __init__(self, detail: str = "Payment processing failed.", error_code: str = "PAYMENT_FAILED") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, error_code=error_code)


class ProviderException(AppException):
    """Raised when an external provider fails or is unavailable."""

    def __init__(self, detail: str = "The external provider service is unavailable.", error_code: str = "PROVIDER_ERROR") -> None:
        super().__init__(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail, error_code=error_code)


class WalletException(AppException):
    """Raised when wallet operations fail."""

    def __init__(self, detail: str = "Wallet operation failed.", error_code: str = "WALLET_ERROR") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, error_code=error_code)


class DuplicateProviderReferenceException(ValidationException):
    """Raised when a provider-scoped provider_reference is duplicated."""

    def __init__(self, detail: str = "Duplicate provider reference detected.", error_code: str = "DUPLICATE_PROVIDER_REFERENCE") -> None:
        super().__init__(detail=detail, error_code=error_code)


class OTPException(AppException):
    """Raised for OTP generation or validation failures."""

    def __init__(self, detail: str = "OTP verification failed.", error_code: str = "OTP_ERROR") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, error_code=error_code)


class GiftCardException(AppException):
    """Raised for gift card operations."""

    def __init__(self, detail: str = "Gift card operation failed.", error_code: str = "GIFT_CARD_ERROR") -> None:
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail, error_code=error_code)


class NotificationException(AppException):
    """Raised when notifications cannot be delivered."""

    def __init__(self, detail: str = "Notification delivery failed.", error_code: str = "NOTIFICATION_ERROR") -> None:
        super().__init__(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail, error_code=error_code)
