from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from app.utils.exceptions import AppException, AuthenticationException, AuthorizationException, DatabaseException, PaymentException, ProviderException, ValidationException
from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Convert unhandled exceptions into standardized API error responses."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            return await self._handle_exception(request, exc)

    async def _handle_exception(self, request: Request, exc: Exception) -> Response:
        from fastapi import HTTPException

        request_id = getattr(request.state, "request_id", None) or getattr(request.state, "correlation_id", None)
        status_code = HTTP_500_INTERNAL_SERVER_ERROR
        message = "An unexpected error occurred."
        errors = None

        if isinstance(exc, HTTPException):
            status_code = exc.status_code
            message = exc.detail if isinstance(exc.detail, str) else "Request failed."
            errors = None
        elif isinstance(exc, RequestValidationError):
            status_code = 422
            message = "Validation failed."
            errors = exc.errors()
        elif isinstance(exc, AppException):
            status_code = exc.status_code
            message = exc.detail
            errors = None
        elif isinstance(exc, (ValueError, TypeError)):
            status_code = 400
            message = "Invalid request data."
            errors = None
        elif isinstance(exc, (ConnectionError, TimeoutError)):
            status_code = 502
            message = "A downstream service is temporarily unavailable."
            errors = None
        else:
            status_code = HTTP_500_INTERNAL_SERVER_ERROR
            message = "An unexpected error occurred."
            errors = None

        payload = error_response(message=message, status_code=status_code, errors=errors)
        if request_id:
            payload["request_id"] = request_id

        self._log_exception(request, exc, status_code)
        return Response(content=self._json_dumps(payload), status_code=status_code, media_type="application/json")

    def _log_exception(self, request: Request, exc: Exception, status_code: int) -> None:
        """Log exception details safely without exposing sensitive data."""
        try:
            logger.exception(
                "request_error",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass

    @staticmethod
    def _json_dumps(payload: dict) -> str:
        import json

        return json.dumps(payload, default=str)
