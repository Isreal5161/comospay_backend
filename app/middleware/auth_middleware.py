from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config.jwt import decode_token
from app.config.redis import get_redis
from app.config.settings import settings
from app.services.auth.token_service import TokenService
from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")


@dataclass(slots=True)
class AuthenticatedUser:
    """Minimal authenticated identity attached to the request context."""

    user_id: str | int
    email: str
    role: str
    token_type: str
    expires_at: str | None = None
    issued_at: str | None = None
    subject: str | None = None

    @classmethod
    def from_claims(cls, payload: dict[str, Any]) -> "AuthenticatedUser":
        """Build a user context from validated JWT claims."""
        return cls(
            user_id=payload.get("user_id") or payload.get("sub") or "unknown",
            email=payload.get("email") or "",
            role=payload.get("role") or "user",
            token_type=str(payload.get("type") or "access"),
            expires_at=payload.get("exp"),
            issued_at=payload.get("iat"),
            subject=str(payload.get("sub") or ""),
        )


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT access tokens and attach authenticated context to each request."""

    def __init__(self, app: Any, public_paths: list[str] | None = None) -> None:
        super().__init__(app)
        self.public_paths = public_paths or self._get_public_paths()

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if self._should_skip_auth(request):
            return await call_next(request)

        token = self._extract_bearer_token(request)
        if not token:
            return self._unauthorized_response(request, "Authentication token is required.")

        try:
            # Use TokenService as the authoritative validator so middleware
            # and higher-level auth utilities enforce the same rules.
            token_service = TokenService()
            claims = token_service.validate_token(token, expected_type="access")
        except Exception as exc:
            self._log_auth_failure(request, str(exc), "token_validation")
            return self._unauthorized_response(request, self._message_for_error(str(exc)))

        # Ensure token isn't revoked. TokenService exposes an async revocation check.
        try:
            revoked = await token_service.check_revoked_token(token=token)
        except Exception:
            # Conservatively deny access if revocation state cannot be confirmed.
            self._log_auth_failure(request, "Token revocation check failed.", "revocation_check_failure")
            return self._unauthorized_response(request, "Authentication token could not be validated.")

        if revoked:
            self._log_auth_failure(request, "Token is revoked.", "token_revocation")
            return self._unauthorized_response(request, "Authentication token has been revoked.")

        if not self._validate_payload(claims):
            self._log_auth_failure(request, "Token payload validation failed.", "payload_validation")
            return self._unauthorized_response(request, "Authentication token is invalid.")

        user_context = AuthenticatedUser.from_claims(claims)
        request.state.user = user_context
        request.state.auth_user = user_context
        request.state.auth_payload = claims
        request.state.token_type = user_context.token_type
        request.state.is_authenticated = True

        return await call_next(request)

    def _should_skip_auth(self, request: Request) -> bool:
        if request.method.lower() == "options":
            return True
        path = self._normalize_path(request.url.path)
        return any(self._matches_path(path, public_path) for public_path in self.public_paths)

    def _extract_bearer_token(self, request: Request) -> str | None:
        header_value = request.headers.get("authorization")
        if not header_value:
            return None
        parts = header_value.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1].strip()
        return token or None

    def _validate_payload(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False

        token_type = str(payload.get("type") or "")
        if token_type != "access":
            return False

        user_id = payload.get("user_id") or payload.get("sub")
        email = payload.get("email")
        role = payload.get("role")
        expires_at = payload.get("exp")

        if not user_id or not email or not role or not expires_at:
            return False
        return True

    async def _is_revoked_token(self, payload: dict[str, Any]) -> bool:
        revoked = payload.get("revoked")
        blacklisted = payload.get("blacklisted")
        if isinstance(revoked, bool) and revoked:
            return True
        if isinstance(blacklisted, bool) and blacklisted:
            return True
        jti = payload.get("jti")
        if not jti:
            return False
        redis_client = await self._get_redis_client()
        if redis_client is None or not hasattr(redis_client, "exists"):
            return False
        return bool(await redis_client.exists(f"{TokenService.REVOCATION_PREFIX}:{jti}"))

    async def _get_redis_client(self) -> Any | None:
        try:
            return await get_redis()
        except Exception:
            return None

    def _unauthorized_response(self, request: Request, message: str) -> JSONResponse:
        payload = error_response(message=message, status_code=401)
        request_id = self._request_id(request)
        if request_id:
            payload["request_id"] = request_id
        return JSONResponse(content=payload, status_code=401)

    def _log_auth_failure(self, request: Request, message: str, reason: str) -> None:
        try:
            logger.warning(
                "authentication_failed",
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

    def _message_for_error(self, error_text: str) -> str:
        if "expired" in error_text.lower():
            return "Authentication token has expired."
        if "invalid" in error_text.lower() or "malformed" in error_text.lower():
            return "Authentication token is invalid."
        return "Authentication token could not be validated."

    def _get_public_paths(self) -> list[str]:
        configured = getattr(settings, "public_routes", None)
        if isinstance(configured, str):
            return [item.strip() for item in configured.split(",") if item.strip()]
        if isinstance(configured, (list, tuple, set)):
            return [str(item).strip() for item in configured if str(item).strip()]
        return [
            "/docs",
            "/openapi.json",
            "/redoc",
            "/health",
            "/healthz",
            "/auth/login",
            "/auth/register",
            "/auth/refresh",
            "/auth/forgot-password",
            "/auth/reset-password",
        ]

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    @staticmethod
    def _matches_path(path: str, pattern: str) -> bool:
        normalized_pattern = AuthMiddleware._normalize_path(pattern)
        if normalized_pattern.endswith("/*"):
            return path.startswith(normalized_pattern[:-1])
        return path == normalized_pattern or path.startswith(f"{normalized_pattern}/")
