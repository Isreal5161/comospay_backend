from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logger import get_logger


logger = get_logger("api")


class AuditMiddleware(BaseHTTPMiddleware):
    """Record security-sensitive request metadata without blocking the main request path."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = getattr(request.state, "request_id", None) or getattr(request.state, "correlation_id", None)
        started_at = time.time()
        response: Response | None = None

        try:
            response = await call_next(request)
            return response
        finally:
            if response is not None:
                self._schedule_audit_event(request, response, request_id, started_at)

    def _schedule_audit_event(self, request: Request, response: Response, request_id: str | None, started_at: float) -> None:
        try:
            event = self._build_audit_event(request, response, request_id, started_at)
            asyncio.get_running_loop().create_task(self._emit_audit_event(event))
        except Exception:
            self._log_audit_failure(request, "Unable to schedule audit event.")

    async def _emit_audit_event(self, event: dict[str, Any]) -> None:
        try:
            logger.info(
                "audit_event",
                extra={
                    "event_type": "audit",
                    "event": event,
                },
            )
        except Exception:
            logger.exception("Failed to emit audit event", extra={"event_type": "audit", "event": event})

    def _build_audit_event(self, request: Request, response: Response, request_id: str | None, started_at: float) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        user_id = getattr(user, "user_id", None) or getattr(user, "id", None) or getattr(user, "sub", None)
        user_role = getattr(user, "role", None) or "anonymous"
        path = request.url.path

        return {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user_id) if user_id is not None else None,
            "user_role": str(user_role),
            "method": request.method,
            "url": str(request.url),
            "client_ip": self._get_client_ip(request),
            "user_agent": request.headers.get("user-agent", ""),
            "status_code": response.status_code,
            "duration_ms": round((time.time() - started_at) * 1000, 3),
            "sensitive": self._is_sensitive_endpoint(path),
            "path": path,
        }

    def _is_sensitive_endpoint(self, path: str) -> bool:
        normalized_path = path.lower()
        sensitive_markers = (
            "/login",
            "/logout",
            "/register",
            "/password",
            "/pin",
            "/wallet/fund",
            "/wallet/transfer",
            "/virtual-account",
            "/airtime",
            "/data",
            "/electricity",
            "/tv",
            "/education",
            "/gift",
            "/admin",
            "/kyc/approve",
            "/kyc/reject",
        )
        return any(marker in normalized_path for marker in sensitive_markers)

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip
        return request.client.host if request.client else "unknown"

    def _log_audit_failure(self, request: Request, message: str) -> None:
        try:
            logger.warning(
                "audit_failure",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "message": message,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
        except Exception:
            pass
