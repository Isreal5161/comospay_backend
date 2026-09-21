from __future__ import annotations

import pytest
from types import SimpleNamespace
from pydantic import SecretStr
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.services.auth.password_service import PasswordService
from app.services.auth.token_service import TokenService


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def set(self, key: str, value: object, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.store[f"{key}:ttl"] = ex
        return True

    async def setex(self, key: str, ttl: int, value: object) -> bool:
        self.store[key] = value
        self.store[f"{key}:ttl"] = ttl
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def get(self, key: str) -> object | None:
        return self.store.get(key)

    async def sadd(self, key: str, *members: object) -> int:
        existing = self.store.get(key)
        if existing is None:
            existing = set()
            self.store[key] = existing
        if not isinstance(existing, set):
            return 0
        added = 0
        for member in members:
            if member not in existing:
                existing.add(member)
                added += 1
        return added

    async def expire(self, key: str, seconds: int) -> bool:
        self.store[f"{key}:ttl"] = seconds
        return True


def _make_request(path: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(name.lower().encode("utf-8"), value.encode("utf-8")) for name, value in (headers or {}).items()],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


async def _dummy_asgi_app(scope: dict[str, object], receive: object, send: object) -> None:
    return None


@pytest.mark.asyncio
async def test_revoke_token_marks_token_as_revoked() -> None:
    fake_redis = FakeRedis()
    fake_settings = SimpleNamespace(
        jwt_secret_key=SecretStr("test-secret"),
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
        refresh_token_expire_days=30,
    )
    service = TokenService(redis_client=fake_redis, settings_obj=fake_settings)

    token = service.create_access_token(subject="user-1")
    result = await service.revoke_token(token)

    assert result["revoked"] is True
    assert await service.check_revoked_token(token) is True


@pytest.mark.asyncio
async def test_revoke_token_returns_false_when_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis() -> object:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.services.auth.token_service.get_redis", fake_get_redis)
    fake_settings = SimpleNamespace(
        jwt_secret_key=SecretStr("test-secret"),
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
        refresh_token_expire_days=30,
    )
    service = TokenService(redis_client=None, settings_obj=fake_settings)
    token = service.create_access_token(subject="user-2")

    result = await service.revoke_token(token)

    assert result["revoked"] is False
    assert result["reason"] == "redis_unavailable"


@pytest.mark.asyncio
async def test_rotate_refresh_token_revokes_previous_refresh_token() -> None:
    fake_redis = FakeRedis()
    fake_settings = SimpleNamespace(
        jwt_secret_key=SecretStr("test-secret"),
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
        refresh_token_expire_days=30,
    )
    service = TokenService(redis_client=fake_redis, settings_obj=fake_settings)

    refresh_token = service.create_refresh_token(subject="user-3")
    result = await service.rotate_refresh_token(refresh_token)

    assert result["revoked_previous"] is True
    assert result["refresh_token"] != refresh_token
    assert await service.check_revoked_token(refresh_token) is True
    assert await service.check_revoked_token(result["refresh_token"]) is False


def test_password_service_accepts_legacy_bcrypt_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "StrongPwd1!"
    legacy_hash = "$2b$12$L1nsNB0tkByroR87eZYIyugxVRyRlFWtsblF5XZKAavdHFPQwuBcO"

    def fake_verify_password(password_arg: str, hashed_password: str) -> bool:
        if password_arg == password and hashed_password == legacy_hash:
            return True
        return False

    monkeypatch.setattr("app.services.auth.password_service._verify_password", fake_verify_password)

    service = PasswordService()

    assert service.verify_password(password, legacy_hash) is True
    assert service.verify_password("wrong-password", legacy_hash) is False


@pytest.mark.parametrize(
    "path",
    [
        "/auth/login",
        "/auth/refresh",
        "/otp/verify",
        "/wallet/balance",
        "/payment/collect",
        "/providers/config/11111111-1111-1111-1111-111111111111",
        "/admin/dashboard",
    ],
)
@pytest.mark.asyncio
async def test_rate_limit_middleware_fail_closed_for_sensitive_routes(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    async def fake_get_redis() -> object:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.middleware.rate_limit_middleware.get_redis", fake_get_redis)
    middleware = RateLimitMiddleware(_dummy_asgi_app)
    request = _make_request(path)

    async def call_next(request: Request) -> Response:
        return Response(content="ok", status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 503


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
@pytest.mark.asyncio
async def test_rate_limit_middleware_fail_open_for_public_routes(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    async def fake_get_redis() -> object:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.middleware.rate_limit_middleware.get_redis", fake_get_redis)
    middleware = RateLimitMiddleware(_dummy_asgi_app)
    request = _make_request(path)

    async def call_next(request: Request) -> Response:
        return Response(content="ok", status_code=200)

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert response.body == b"ok"
