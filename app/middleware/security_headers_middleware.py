from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config.settings import settings
from app.utils.logger import get_logger


logger = get_logger("api")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach modern security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        try:
            self._apply_headers(response)
        except Exception:
            try:
                logger.warning("security_headers_middleware_failed", extra={"path": request.url.path})
            except Exception:
                pass
        return response

    def _apply_headers(self, response: Response) -> None:
        header_config = self._get_header_config()
        for name, value in header_config.items():
            if self._should_set_header(response, name):
                response.headers[name] = value

    def _get_header_config(self) -> dict[str, str]:
        return {
            "Strict-Transport-Security": self._get_value("security_hsts", "max-age=31536000; includeSubDomains"),
            "X-Content-Type-Options": self._get_value("security_x_content_type_options", "nosniff"),
            "X-Frame-Options": self._get_value("security_x_frame_options", "DENY"),
            "Referrer-Policy": self._get_value("security_referrer_policy", "no-referrer"),
            "Permissions-Policy": self._get_value("security_permissions_policy", "geolocation=(), microphone=(), camera=()"),
            "Cross-Origin-Opener-Policy": self._get_value("security_coop", "same-origin"),
            "Cross-Origin-Resource-Policy": self._get_value("security_corp", "same-origin"),
            "Cross-Origin-Embedder-Policy": self._get_value("security_coep", "require-corp"),
            "Content-Security-Policy": self._get_value("security_csp", self._default_csp()),
        }

    def _get_value(self, setting_name: str, default: str) -> str:
        value = getattr(settings, setting_name, None)
        return default if value in (None, "") else str(value)

    def _default_csp(self) -> str:
        return (
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "img-src 'self' data:; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline';"
        )

    def _should_set_header(self, response: Response, name: str) -> bool:
        if not self._is_enabled(name):
            return False
        existing = response.headers.get(name)
        if existing:
            return self._allow_overwrite(name)
        return True

    def _is_enabled(self, name: str) -> bool:
        enabled_setting = getattr(settings, f"security_{name.lower().replace('-', '_')}_enabled", None)
        if enabled_setting is None:
            return True
        return bool(enabled_setting)

    def _allow_overwrite(self, name: str) -> bool:
        overwrite_setting = getattr(settings, f"security_{name.lower().replace('-', '_')}_overwrite", None)
        return overwrite_setting is True

    def _header_name_to_setting(self, name: str) -> str:
        return name.lower().replace("-", "_")
