from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logger import get_logger


logger = get_logger("api")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log request and response metadata for FastAPI applications."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start_time = time.perf_counter()
        request_id = request.headers.get("x-request-id") or request.headers.get("x-correlation-id") or self._generate_request_id()
        request.state.request_id = request_id
        request.state.correlation_id = request_id

        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            try:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                status_code = getattr(response, "status_code", 500)
                method = request.method
                path = request.url.path
                client_ip = self._get_client_ip(request)
                user_agent = request.headers.get("user-agent", "")

                logger.info(
                    "request_completed",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "client_ip": client_ip,
                        "user_agent": user_agent,
                        "status_code": status_code,
                        "response_time_ms": duration_ms,
                    },
                )
            except Exception:
                pass

    @staticmethod
    def _generate_request_id() -> str:
        """Generate a lightweight request ID for tracing."""
        import secrets

        return f"req-{secrets.token_hex(8)}"

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract the most relevant client IP from request headers."""
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
