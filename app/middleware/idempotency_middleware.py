from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response as StarletteResponse

from app.config.redis import get_redis
from app.config.settings import settings
from app.utils.logger import get_logger
from app.utils.response import error_response


logger = get_logger("api")


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Prevent duplicate processing of sensitive mutating requests using Redis-backed records."""

    protected_methods: set[str] = {"POST", "PUT", "PATCH"}
    protected_path_markers: tuple[str, ...] = (
        "/wallet",
        "/transfer",
        "/airtime",
        "/data",
        "/electricity",
        "/tv",
        "/education",
        "/gift",
        "/payment",
        "/admin",
        "/kyc",
    )

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not self._should_protect(request):
            return await call_next(request)

        idempotency_key = self._extract_idempotency_key(request)
        if not idempotency_key:
            return self._bad_request_response(request, "Idempotency-Key header is required for this request.")

        try:
            redis_client = await get_redis()
        except Exception as exc:
            self._log_redis_failure(request, exc)
            if self._fail_open():
                return await call_next(request)
            return self._service_unavailable_response(request, "Idempotency service is temporarily unavailable.")

        request_body = await request.body()
        fingerprint = self._build_fingerprint(request, request_body)
        cache_key = self._build_cache_key(request, idempotency_key)

        try:
            existing_payload = await redis_client.get(cache_key)
            if existing_payload:
                record = json.loads(existing_payload)
                if record.get("fingerprint") != fingerprint:
                    return self._conflict_response(request, "Idempotency-Key was already used with a different request payload.")
                return self._build_stored_response(request, record)

            response = await call_next(request)
            stored_response = await self._capture_response(response)
            record = {
                "fingerprint": fingerprint,
                "status_code": stored_response["status_code"],
                "body": stored_response["body"],
                "headers": stored_response["headers"],
                "content_type": stored_response["content_type"],
                "timestamp": stored_response["timestamp"],
            }
            await redis_client.setex(cache_key, self._ttl_seconds(), json.dumps(record))
            return stored_response["response"]
        except Exception as exc:
            self._log_redis_failure(request, exc)
            if self._fail_open():
                return await call_next(request)
            return self._service_unavailable_response(request, "Idempotency service is temporarily unavailable.")

    def _should_protect(self, request: Request) -> bool:
        if request.method.upper() not in self.protected_methods:
            return False
        path = request.url.path.lower()
        return any(marker in path for marker in self.protected_path_markers)

    def _extract_idempotency_key(self, request: Request) -> str | None:
        value = request.headers.get("idempotency-key") or request.headers.get("Idempotency-Key")
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def _build_cache_key(self, request: Request, idempotency_key: str) -> str:
        return f"idempotency:{request.method.upper()}:{request.url.path}:{idempotency_key}"

    def _build_fingerprint(self, request: Request, body: bytes) -> str:
        payload = {
            "method": request.method.upper(),
            "path": request.url.path,
            "query": request.url.query,
            "body": base64.b64encode(body).decode("utf-8"),
            "headers": {key: value for key, value in request.headers.items() if key.lower() in {"content-type", "authorization"}},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    async def _capture_response(self, response: Response) -> dict[str, Any]:
        body_bytes = b""
        response_headers = {key: value for key, value in response.headers.items()}
        response_content_type = response.headers.get("content-type", "application/json")

        if hasattr(response, "body") and response.body is not None:
            body_bytes = response.body
        elif hasattr(response, "body_iterator"):
            chunks: list[bytes] = []
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunks.append(chunk.encode("utf-8"))
                else:
                    chunks.append(chunk)
            body_bytes = b"".join(chunks)

        if body_bytes:
            response_body = base64.b64encode(body_bytes).decode("utf-8")
        else:
            response_body = ""

        stored_response = StarletteResponse(
            content=body_bytes,
            status_code=response.status_code,
            headers=response_headers,
            media_type=response_content_type,
        )
        return {
            "status_code": response.status_code,
            "body": response_body,
            "headers": response_headers,
            "content_type": response_content_type,
            "timestamp": self._timestamp(),
            "response": stored_response,
        }

    def _build_stored_response(self, request: Request, record: dict[str, Any]) -> Response:
        response = StarletteResponse(
            content=base64.b64decode(record.get("body", "").encode("utf-8")) if record.get("body") else b"",
            status_code=int(record.get("status_code", 200)),
            headers=dict(record.get("headers", {})),
            media_type=str(record.get("content_type", "application/json")),
        )
        response.headers["X-Idempotency-Replayed"] = "true"
        return response

    def _bad_request_response(self, request: Request, message: str) -> JSONResponse:
        payload = error_response(message=message, status_code=400)
        payload["request_id"] = self._request_id(request)
        return JSONResponse(content=payload, status_code=400)

    def _conflict_response(self, request: Request, message: str) -> JSONResponse:
        payload = error_response(message=message, status_code=409)
        payload["request_id"] = self._request_id(request)
        return JSONResponse(content=payload, status_code=409)

    def _service_unavailable_response(self, request: Request, message: str) -> JSONResponse:
        payload = error_response(message=message, status_code=503)
        payload["request_id"] = self._request_id(request)
        return JSONResponse(content=payload, status_code=503)

    def _fail_open(self) -> bool:
        value = getattr(settings, "idempotency_fail_open", None)
        return True if value is None else bool(value)

    def _ttl_seconds(self) -> int:
        value = getattr(settings, "idempotency_ttl_seconds", None)
        if value is None:
            value = getattr(settings, "redis_cache_ttl", 86400)
        return int(value)

    def _request_id(self, request: Request) -> str | None:
        return getattr(request.state, "request_id", None) or getattr(request.state, "correlation_id", None)

    def _timestamp(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _log_redis_failure(self, request: Request, exc: Exception) -> None:
        try:
            logger.warning(
                "idempotency_redis_failure",
                extra={
                    "request_id": self._request_id(request),
                    "path": request.url.path,
                    "method": request.method,
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass
