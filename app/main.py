from __future__ import annotations

import importlib
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

try:
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
    _HAS_PROXY_HEADERS_MIDDLEWARE = True
except ImportError:  # pragma: no cover - compatibility fallback
    _HAS_PROXY_HEADERS_MIDDLEWARE = False
    class ProxyHeadersMiddleware(BaseHTTPMiddleware):
        """Fallback proxy-header middleware for environments without the standalone module."""

        async def dispatch(self, request, call_next):
            return await call_next(request)

from app.config.database import engine
from app.config.redis import connect_redis, disconnect_redis
from app.config.settings import settings
from app.docs.openapi import configure_openapi
from app.middleware.admin_middleware import AdminMiddleware
from app.middleware.audit_middleware import AuditMiddleware
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.error_middleware import ErrorHandlingMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.middleware.request_id_middleware import RequestIDMiddleware
from app.middleware.security_headers_middleware import SecurityHeadersMiddleware
from app.utils.logger import get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="CosmozPay API",
        version=settings.app_version,
        description="Enterprise-grade fintech backend for wallets, payments, and bill services.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    configure_openapi(app, api_version=settings.app_version)

    app.state.settings = settings

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_cors_origins(),
        allow_credentials=_get_cors_setting("allow_credentials", True),
        allow_methods=_get_cors_setting("allow_methods", ["*"]),
        allow_headers=_get_cors_setting("allow_headers", ["*"]),
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_get_trusted_hosts(),
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware, public_paths=_get_public_paths())
    # Admin allowed roles may be configured via settings.ADMIN_ALLOWED_ROLES (comma-separated or list).
    configured_roles = getattr(settings, "admin_allowed_roles", None)
    if isinstance(configured_roles, str):
        configured_roles = [r.strip() for r in configured_roles.split(",") if r.strip()]
    allowed_roles = configured_roles or ["super_admin", "admin"]
    app.add_middleware(AdminMiddleware, allowed_roles=allowed_roles)
    app.add_middleware(AuditMiddleware)

    if _HAS_PROXY_HEADERS_MIDDLEWARE:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    else:
        app.add_middleware(ProxyHeadersMiddleware)

    _register_routes(app)

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Return basic service metadata."""
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "status": "ok",
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Return a simple health response."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Return readiness information."""
        return {"status": "ready"}

    @app.get("/live")
    async def live() -> dict[str, Any]:
        """Return liveness information."""
        return {"status": "alive"}

    @app.on_event("startup")
    async def startup_event() -> None:
        """Initialize shared infrastructure dependencies."""
        await connect_redis()
        await _verify_database_connection()
        # Start background worker for virtual account provisioning retries
        try:
            from app.jobs.virtual_account_retry_job import start_background_retry_worker

            await start_background_retry_worker(app)
        except Exception:
            logger.exception("Failed to start virtual-account retry background worker")

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        """Clean up shared infrastructure dependencies."""
        try:
            from app.jobs.virtual_account_retry_job import stop_background_retry_worker

            await stop_background_retry_worker(app)
        except Exception:
            logger.exception("Failed to stop virtual-account retry background worker")
        await disconnect_redis()
        engine.dispose()

    return app


def _register_routes(app: FastAPI) -> None:
    """Register routers from the routes package using a simple registry."""
    route_modules: list[tuple[str, str]] = [
        ("Authentication", "app.routes.auth_routes"),
        ("Users", "app.routes.user_routes"),
        ("Wallet", "app.routes.wallet_routes"),
        ("Payments", "app.routes.payment_routes"),
        ("Airtime", "app.routes.airtime_routes"),
        ("Data", "app.routes.data_routes"),
        ("Electricity", "app.routes.electricity_routes"),
        ("TV", "app.routes.tv_routes"),
        ("Education", "app.routes.education_routes"),
        ("Gift Cards", "app.routes.giftcard_routes"),
        ("Notifications", "app.routes.notification_routes"),
        ("Provider", "app.routes.provider_routes"),
        ("Admin", "app.routes.admin_routes"),
        ("Webhook", "app.routes.webhook_routes"),
    ]

    for _, module_name in route_modules:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue

        router = getattr(module, "router", None)
        if router is None:
            from fastapi import APIRouter

            router = APIRouter()

        app.include_router(router)


async def _verify_database_connection() -> None:
    """Ensure the configured database engine is reachable."""
    async with engine.begin() as connection:
        await connection.execute(text("SELECT 1"))


def _get_cors_origins() -> list[str]:
    """Return CORS origins from configuration, supporting comma-delimited values."""
    origins = _get_cors_setting("allow_origins", ["*"])
    if isinstance(origins, str):
        resolved = [origin.strip() for origin in origins.split(",") if origin.strip()]
    else:
        resolved = [str(origin).strip() for origin in origins if str(origin).strip()]

    if getattr(settings, "app_env", "development") == "production":
        if not resolved or any(origin == "*" for origin in resolved):
            raise RuntimeError(
                "In production, CORS allow_origins must be explicitly configured and must not contain wildcard '*'."
            )
    return resolved


def _get_cors_setting(name: str, default: Any) -> Any:
    """Read a CORS setting from configuration with a safe fallback."""
    value = getattr(settings, f"cors_{name}", None)
    if value in (None, ""):
        return default
    return value


def _get_trusted_hosts() -> list[str]:
    """Return trusted hosts from configuration with a safe default."""
    value = getattr(settings, "trusted_hosts", None)
    if isinstance(value, str):
        resolved = [host.strip() for host in value.split(",") if host.strip()]
    elif value in (None, ""):
        resolved = []
    else:
        resolved = [str(host).strip() for host in value if str(host).strip()]

    if getattr(settings, "app_env", "development") == "production":
        if not resolved or any(host == "*" for host in resolved):
            raise RuntimeError(
                "In production, trusted_hosts must be explicitly configured and must not contain wildcard '*'."
            )

    if not resolved:
        return ["*"]
    return resolved


def _get_public_paths() -> list[str]:
    """Return public routes that should bypass authentication."""
    value = getattr(settings, "public_routes", None)
    if value in (None, ""):
        return [
            "/docs",
            "/openapi.json",
            "/redoc",
            "/health",
            "/healthz",
            "/",
            "/auth/login",
            "/auth/register",
            "/auth/refresh",
        ]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


app = create_app()
