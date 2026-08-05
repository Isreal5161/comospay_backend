from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")


class AdminMiddleware(BaseHTTPMiddleware):
    """Authorize requests for administrator-only routes using request-scoped user context."""

    def __init__(self, app: Any, allowed_roles: Iterable[str] | None = None) -> None:
        super().__init__(app)
        self.allowed_roles = {self._normalize_role(role) for role in (allowed_roles or self._default_roles())}

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if self._should_skip(request):
            return await call_next(request)

        user = getattr(request.state, "user", None)
        if user is None:
            self._log_authorization_failure(request, "Missing authenticated user context.", "missing_user")
            return self._forbidden_response(request, "Access denied.")

        role = self._extract_role(user)
        if not self._is_authorized(role):
            self._log_authorization_failure(request, f"Role '{role}' is not authorized.", "role_denied")
            return self._forbidden_response(request, "You are not authorized to access this resource.")

        return await call_next(request)

    def _should_skip(self, request: Request) -> bool:
        path = self._normalize_path(request.url.path)
        if request.method.lower() == "options":
            return True
        if not path.startswith("/admin"):
            return True
        return False

    def _extract_role(self, user: Any) -> str:
        role = getattr(user, "role", None)
        if isinstance(role, str) and role.strip():
            return self._normalize_role(role)
        return ""

    def _is_authorized(self, role: str) -> bool:
        if not role:
            return False
        return role in self.allowed_roles

    def _forbidden_response(self, request: Request, message: str) -> JSONResponse:
        payload = error_response(message=message, status_code=403)
        request_id = self._request_id(request)
        if request_id:
            payload["request_id"] = request_id
        return JSONResponse(content=payload, status_code=403)

    def _log_authorization_failure(self, request: Request, message: str, reason: str) -> None:
        try:
            logger.warning(
                "authorization_failed",
                extra={
                    "request_id": self._request_id(request),
                    "reason": reason,
                    "message": message,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
        except Exception:
            pass

    def _request_id(self, request: Request) -> str | None:
        return getattr(request.state, "request_id", None) or getattr(request.state, "correlation_id", None)

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    @staticmethod
    def _normalize_role(role: str) -> str:
        return role.strip().lower().replace(" ", "_")

    @staticmethod
    def _default_roles() -> tuple[str, ...]:
        return ("super_admin", "admin", "support", "auditor")
