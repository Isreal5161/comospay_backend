from __future__ import annotations

import secrets
import uuid
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logger import get_logger


logger = get_logger("api")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request-scoped correlation ID to requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = self._resolve_request_id(request)
        request.state.request_id = request_id
        request.state.correlation_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    def _resolve_request_id(self, request: Request) -> str:
        header_value = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
        if self._is_valid_request_id(header_value):
            return header_value
        return self._generate_request_id()

    @staticmethod
    def _is_valid_request_id(value: str | None) -> bool:
        if not value:
            return False
        stripped = value.strip()
        if not stripped:
            return False
        return len(stripped) <= 128

    @staticmethod
    def _generate_request_id() -> str:
        """Generate a UUID4 request ID with minimal overhead."""
        return str(uuid.uuid4())

    def _log_failure(self, request: Request, message: str) -> None:
        try:
            logger.warning(
                "request_id_generation_failed",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "message": message,
                    "path": request.url.path,
                },
            )
        except Exception:
            pass
