from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config.redis import get_redis
from app.config.settings import settings
from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_LIMITS = {
    "global": 100,
    "auth": 20,
    "otp": 10,
    "wallet": 50,
    "payment": 30,
    "provider": 50,
    "admin": 100,
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Distributed Redis-backed rate limiting middleware for FastAPI."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            limit_result = await self._evaluate_request(request)
        except Exception as exc:
            return await self._handle_redis_failure(request, call_next, exc)

        if not limit_result.allowed:
            return self._build_rate_limited_response(request, limit_result)

        response = await call_next(request)
        return self._apply_headers(response, limit_result)

    async def _evaluate_request(self, request: Request) -> "RateLimitResult":
        client_id = self._get_client_id(request)
        window_seconds = self._get_window_seconds()
        buckets = self._get_buckets(request)

        results: list[RateLimitResult] = []
        for bucket_name, limit in buckets:
            key = self._build_key(bucket_name, client_id)
            result = await self._check_bucket(request, key, limit, window_seconds)
            results.append(result)
            if not result.allowed:
                self._log_violation(request, bucket_name, client_id, limit, result)
                return result

        return self._merge_results(results)

    async def _check_bucket(self, request: Request, key: str, limit: int, window_seconds: int) -> "RateLimitResult":
        redis_client = await get_redis()
        script = """
        local current = redis.call('INCR', KEYS[1])
        if current == 1 then
            redis.call('EXPIRE', KEYS[1], ARGV[1])
        end
        return {current, redis.call('TTL', KEYS[1])}
        """
        raw_result = await redis_client.eval(script, 1, key, window_seconds)
        current_count = int(raw_result[0]) if raw_result else 1
        ttl = int(raw_result[1]) if len(raw_result) > 1 else window_seconds

        remaining = max(limit - current_count, 0)
        allowed = current_count <= limit
        reset_at = int(time.time()) + ttl
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=max(ttl, 1),
            bucket_name=self._bucket_name_from_key(key),
        )

    async def _handle_redis_failure(self, request: Request, call_next: Callable[[Request], Awaitable[Response]], exc: Exception) -> Response:
        self._log_failure(request, exc)
        fail_open = bool(self._get_setting("rate_limit_fail_open", True))
        if fail_open:
            return await call_next(request)
        return JSONResponse(
            content=error_response("Rate limiting service is temporarily unavailable.", status_code=503),
            status_code=503,
        )

    def _apply_headers(self, response: Response, result: "RateLimitResult") -> Response:
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(result.reset_at)
        response.headers["Retry-After"] = str(result.retry_after)
        return response

    def _build_rate_limited_response(self, request: Request, result: "RateLimitResult") -> JSONResponse:
        payload = error_response("Too many requests. Please retry later.", status_code=429)
        payload["request_id"] = self._get_request_id(request)
        response = JSONResponse(content=payload, status_code=429)
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = "0"
        response.headers["X-RateLimit-Reset"] = str(result.reset_at)
        response.headers["Retry-After"] = str(result.retry_after)
        return response

    def _get_buckets(self, request: Request) -> list[tuple[str, int]]:
        global_limit = self._get_limit("global")
        buckets: list[tuple[str, int]] = [("global", global_limit)]

        path = request.url.path.lower()
        if path.startswith("/auth") or "/auth/" in path or path == "/login" or path == "/register":
            buckets.append(("auth", self._get_limit("auth")))
        elif "/otp" in path or path.startswith("/otp"):
            buckets.append(("otp", self._get_limit("otp")))
        elif path.startswith("/wallet") or "/wallet/" in path:
            buckets.append(("wallet", self._get_limit("wallet")))
        elif path.startswith("/payment") or "/payment/" in path:
            buckets.append(("payment", self._get_limit("payment")))
        elif path.startswith("/provider") or "/provider/" in path:
            buckets.append(("provider", self._get_limit("provider")))
        elif path.startswith("/admin") or "/admin/" in path:
            buckets.append(("admin", self._get_limit("admin")))
        return buckets

    def _get_limit(self, bucket: str) -> int:
        return int(self._get_setting(f"rate_limit_{bucket}", DEFAULT_LIMITS[bucket]))

    def _get_window_seconds(self) -> int:
        return int(self._get_setting("rate_limit_window_seconds", DEFAULT_WINDOW_SECONDS))

    def _get_setting(self, name: str, default: int | bool) -> int | bool:
        value = getattr(settings, name, None)
        return default if value is None else value

    def _build_key(self, bucket: str, client_id: str) -> str:
        return f"rate-limit:{bucket}:{client_id}"

    def _get_client_id(self, request: Request) -> str:
        user = getattr(request.state, "user", None)
        if user is not None:
            user_id = getattr(user, "id", None) or getattr(user, "user_id", None)
            if user_id:
                return f"user:{user_id}"
        if hasattr(request, "scope"):
            scope_user = request.scope.get("user")
            if scope_user is not None:
                user_id = getattr(scope_user, "id", None) or getattr(scope_user, "user_id", None)
                if user_id:
                    return f"user:{user_id}"
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return f"ip:{forwarded_for.split(',')[0].strip()}"
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return f"ip:{real_ip}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    def _get_request_id(self, request: Request) -> str | None:
        return getattr(request.state, "request_id", None) or getattr(request.state, "correlation_id", None)

    def _log_violation(self, request: Request, bucket_name: str, client_id: str, limit: int, result: "RateLimitResult") -> None:
        request_id = self._get_request_id(request)
        try:
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "request_id": request_id,
                    "bucket": bucket_name,
                    "client_id": client_id,
                    "limit": limit,
                    "remaining": result.remaining,
                    "path": request.url.path,
                },
            )
        except Exception:
            pass

    def _log_failure(self, request: Request, exc: Exception) -> None:
        request_id = self._get_request_id(request)
        try:
            logger.warning(
                "rate_limit_unavailable",
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass

    def _bucket_name_from_key(self, key: str) -> str:
        parts = key.split(":")
        return parts[2] if len(parts) > 2 else "global"

    @staticmethod
    def _merge_results(results: list["RateLimitResult"]) -> "RateLimitResult":
        if not results:
            return RateLimitResult(allowed=True, limit=0, remaining=0, reset_at=int(time.time()), retry_after=1, bucket_name="global")
        effective = min(results, key=lambda item: item.limit)
        return RateLimitResult(
            allowed=True,
            limit=effective.limit,
            remaining=min(item.remaining for item in results),
            reset_at=min(item.reset_at for item in results),
            retry_after=min(item.retry_after for item in results),
            bucket_name=effective.bucket_name,
        )


class RateLimitResult:
    """Represents the result of a rate limit evaluation."""

    def __init__(self, *, allowed: bool, limit: int, remaining: int, reset_at: int, retry_after: int, bucket_name: str) -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.reset_at = reset_at
        self.retry_after = retry_after
        self.bucket_name = bucket_name

    def _bucket_name_from_key(self, key: str) -> str:
        parts = key.split(":")
        return parts[2] if len(parts) > 2 else "global"

    @staticmethod
    def _merge_results(results: list["RateLimitResult"]) -> "RateLimitResult":
        if not results:
            return RateLimitResult(allowed=True, limit=0, remaining=0, reset_at=int(time.time()), retry_after=1, bucket_name="global")
        effective = min(results, key=lambda item: item.limit)
        return RateLimitResult(
            allowed=True,
            limit=effective.limit,
            remaining=min(item.remaining for item in results),
            reset_at=min(item.reset_at for item in results),
            retry_after=min(item.retry_after for item in results),
            bucket_name=effective.bucket_name,
        )
