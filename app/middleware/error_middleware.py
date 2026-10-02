from __future__ import annotations

import json
import re
import traceback
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR

from app.utils.exceptions import AppException, AuthenticationException, AuthorizationException, DatabaseException, PaymentException, ProviderException, ValidationException
from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|access[_ -]?token|refresh[_ -]?token|token|"
    r"authorization|cookie|database[_ -]?url|redis[_ -]?url|jwt[_ -]?secret(?:[_ -]?key)?|"
    r"api[_ -]?key|smtp[_ -]?password|secret)\b(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_URI_CREDENTIAL_PATTERN = re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+@")
_BEARER_CREDENTIAL_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/-]+=*")


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
        exception_message = self._redact_sensitive_text(str(exc))
        formatted_traceback = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        logger.exception(
            "request_error %s",
            json.dumps(
                {
                    "request_id": getattr(request.state, "request_id", None),
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "error_type": type(exc).__name__,
                    "exception_message": exception_message,
                    "traceback": self._redact_sensitive_text(formatted_traceback),
                }
            ),
        )

    @staticmethod
    def _redact_sensitive_text(value: str) -> str:
        """Redact common credential forms from exception text before logging."""
        value = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[REDACTED]", value)
        value = _URI_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]@", value)
        return _BEARER_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]", value)

    @staticmethod
    def _json_dumps(payload: dict) -> str:
        import json

        return json.dumps(payload, default=str)
