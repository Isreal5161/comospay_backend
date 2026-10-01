from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.admin_middleware import AdminMiddleware
from app.middleware.auth_middleware import AuthMiddleware
from app.controllers.auth_controller import AuthController, TokenRefreshRequest
from app.routes.auth_routes import refresh_token as user_refresh_route
from app.services.auth_service import AuthService
from app.services.auth.otp_service import InvalidOtpException, OTPService
from app.services.auth.token_service import TokenService
from app.services.admin_service import AdminService
from app.config.settings import settings
from app.utils.exceptions import AuthenticationException


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def set(self, key: str, value: object, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: object) -> bool:
        self.store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return int(key in self.store)

    async def get(self, key: str) -> object | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)

    async def sadd(self, key: str, *values: object) -> int:
        collection = self.store.setdefault(key, set())
        if not isinstance(collection, set):
            return 0
        before = len(collection)
        collection.update(values)
        return len(collection) - before

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.store


def make_token_service(redis: FakeRedis) -> TokenService:
    settings = SimpleNamespace(
        jwt_secret_key=SecretStr("test-secret"),
        jwt_algorithm="HS256",
        access_token_expire_minutes=60,
        refresh_token_expire_days=30,
    )
    return TokenService(redis_client=redis, settings_obj=settings)


def make_admin() -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        email="admin@example.com",
        username="root-admin",
        password_hash="stored-hash",
        first_name="Root",
        last_name="Admin",
        phone=None,
        role="admin",
        status="active",
        is_active=True,
        is_super_admin=False,
        mfa_enabled=False,
        two_factor_secret="must-not-leak",
        last_login_at=None,
        last_password_change_at=None,
        failed_login_attempts=0,
        locked_until=None,
        created_at=now,
        updated_at=now,
    )


class FakeAdminRepository:
    def __init__(self, admin: SimpleNamespace | None) -> None:
        self.admin = admin
        self.username_lookups: list[str] = []

    async def get_by_email(self, email: str) -> SimpleNamespace | None:
        return self.admin if self.admin and self.admin.email == email else None

    async def get_by_username(self, username: str) -> SimpleNamespace | None:
        self.username_lookups.append(username)
        return self.admin if self.admin and self.admin.username == username else None

    async def get_by_id(self, admin_id: object) -> SimpleNamespace | None:
        return self.admin if self.admin and str(self.admin.id) == str(admin_id) else None

    async def update(self, admin: SimpleNamespace, **fields: object) -> SimpleNamespace:
        for field, value in fields.items():
            setattr(admin, field, value)
        return admin


class FakeUserRepository:
    def __init__(self, user: SimpleNamespace) -> None:
        self.user = user
        self.lookups: list[UUID] = []

    async def get_by_id(self, user_id: UUID) -> SimpleNamespace | None:
        self.lookups.append(user_id)
        return self.user if self.user.id == user_id else None


class FakePasswordService:
    def verify_password(self, password: str, password_hash: str) -> bool:
        return password == "correct-password" and password_hash == "stored-hash"


class FakeSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}
        self.counter = 0

    async def create_session(self, *, user_id: str, refresh_token_family_id: str, metadata: dict[str, object]) -> dict[str, object]:
        self.counter += 1
        session_id = f"session-{self.counter}"
        session = {
            "session_id": session_id,
            "user_id": user_id,
            "refresh_token_family_id": refresh_token_family_id,
            "status": "active",
            "metadata": metadata,
        }
        self.sessions[session_id] = session
        return session

    async def validate_session(self, *, session_id: str) -> dict[str, object]:
        session = self.sessions.get(session_id)
        if session is None or session["status"] != "active":
            raise AuthenticationException("Session is invalid.")
        return session

    async def revoke_session(self, *, session_id: str, reason: str) -> dict[str, object]:
        session = self.sessions[session_id]
        session["status"] = "revoked"
        return session


class FakeOtpService:
    admin_id: str
    sent: dict[str, Any]

    async def send_otp(self, **kwargs: Any) -> dict[str, str]:
        self.sent = kwargs
        return {"reference_id": "mfa-reference"}

    async def verify_otp(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["reference_id"] == "mfa-reference"
        assert kwargs["purpose"] == "login_verification"
        return {
            "verified": True,
            "purpose": "login_verification",
            "user_id": str(self.admin_id),
            "metadata": {"identity_type": "admin", "admin_id": str(self.admin_id)},
        }


def make_admin_auth_service(
    admin: SimpleNamespace | None = None,
    *,
    mfa_service: FakeOtpService | None = None,
) -> tuple[AuthService, FakeAdminRepository, FakeSessionService, TokenService]:
    redis = FakeRedis()
    token_service = make_token_service(redis)
    admin_repository = FakeAdminRepository(admin)
    session_service = FakeSessionService()
    service = AuthService(
        admin_repository=admin_repository,
        password_service=FakePasswordService(),
        otp_service=mfa_service,
        token_service=token_service,
        session_service=session_service,
    )
    return service, admin_repository, session_service, token_service


def make_user_refresh_service(
    *,
    identity_type: str | None = None,
) -> tuple[AuthService, FakeUserRepository, TokenService, str]:
    redis = FakeRedis()
    token_service = make_token_service(redis)
    user_id = UUID("22222222-2222-4222-8222-222222222222")
    user = SimpleNamespace(
        id=user_id,
        email="user@example.com",
        email_verified=True,
        is_active=True,
        is_blocked=False,
        is_suspended=False,
    )
    user_repository = FakeUserRepository(user)
    service = AuthService(
        user_repository=user_repository,
        session=object(),
        token_service=token_service,
    )
    claims = {"user_id": str(user_id), "email": user.email, "role": "user"}
    if identity_type is not None:
        claims["identity_type"] = identity_type
    refresh_token = token_service.create_refresh_token(str(user_id), extra_claims=claims)
    return service, user_repository, token_service, refresh_token


def make_refresh_controller(service: AuthService) -> SimpleNamespace:
    async def refresh(payload: TokenRefreshRequest) -> dict[str, Any]:
        return await service.refresh_token(refresh_token=payload.refresh_token)

    return SimpleNamespace(refresh_token=refresh)


@pytest.mark.asyncio
async def test_user_refresh_route_rejects_admin_token_before_user_lookup() -> None:
    service, user_repository, _, admin_refresh_token = make_user_refresh_service(identity_type="admin")
    controller = cast(AuthController, make_refresh_controller(service))

    with pytest.raises(AuthenticationException, match="Refresh token is invalid"):
        await user_refresh_route(
            TokenRefreshRequest(refresh_token=admin_refresh_token),
            controller,
        )

    assert user_repository.lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_type", [None, "user"])
async def test_user_refresh_route_accepts_legacy_and_explicit_user_tokens(identity_type: str | None) -> None:
    service, user_repository, token_service, refresh_token = make_user_refresh_service(identity_type=identity_type)
    controller = cast(AuthController, make_refresh_controller(service))

    result = await user_refresh_route(TokenRefreshRequest(refresh_token=refresh_token), controller)

    claims = token_service.validate_token(result["access_token"], expected_type="access")
    assert claims["sub"] == str(user_repository.user.id)
    assert claims.get("identity_type") is None
    assert user_repository.lookups == [user_repository.user.id]


@pytest.mark.asyncio
async def test_admin_refresh_endpoint_rejects_user_token() -> None:
    service, _, _, user_refresh_token = make_user_refresh_service(identity_type="user")
    service.admin_repository = FakeAdminRepository(None)

    with pytest.raises(AuthenticationException, match="invalid"):
        await service.refresh_admin_token(refresh_token=user_refresh_token)


@pytest.mark.asyncio
async def test_otp_verification_is_bound_to_the_challenge_owner() -> None:
    redis = FakeRedis()
    service = OTPService(redis_client=redis)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    redis.store[service._otp_key("admin-challenge")] = json.dumps(
        {
            "purpose": "login_verification",
            "user_id": "admin-uuid",
            "otp_hash": service._hash_secret("123456"),
            "expires_at": expires_at.isoformat(),
            "attempts": 0,
            "metadata": {"identity_type": "admin"},
        }
    )

    with pytest.raises(InvalidOtpException):
        await service.verify_otp(
            reference_id="admin-challenge",
            otp_code="123456",
            purpose="login_verification",
            user_id="different-admin-uuid",
        )

    result = await service.verify_otp(
        reference_id="admin-challenge",
        otp_code="123456",
        purpose="login_verification",
        user_id="admin-uuid",
        include_context=True,
    )
    assert result["user_id"] == "admin-uuid"
    assert result["metadata"] == {"identity_type": "admin"}


@pytest.mark.asyncio
async def test_refresh_rotation_preserves_identity_claims_and_checks_family_revocation() -> None:
    redis = FakeRedis()
    service = make_token_service(redis)
    family_id = service.generate_token_family_id()
    refresh = service.create_refresh_token(
        "admin-uuid",
        family_id=family_id,
        extra_claims={"identity_type": "admin", "user_id": "admin-uuid", "role": "admin", "email": "admin@example.com"},
    )

    rotated = await service.rotate_refresh_token(
        refresh,
        extra_claims={"identity_type": "admin", "user_id": "admin-uuid", "role": "admin", "email": "admin@example.com"},
        require_revocation=True,
    )
    claims = service.validate_token(rotated["refresh_token"], expected_type="refresh")
    assert claims["identity_type"] == "admin"
    assert claims["sub"] == "admin-uuid"
    assert claims["role"] == "admin"
    assert await service.check_revoked_token(refresh, fail_closed=True) is True

    await service.revoke_token_family(family_id, ttl_seconds=60)
    assert await service.check_revoked_token_family(family_id) is True

    with pytest.raises(Exception):
        await service.rotate_refresh_token(
            rotated["refresh_token"],
            extra_claims={"identity_type": "admin", "user_id": "admin-uuid", "role": "admin", "email": "admin@example.com"},
            require_revocation=True,
        )


@pytest.mark.asyncio
async def test_admin_login_issues_admin_uuid_and_safe_response() -> None:
    admin = make_admin()
    service, repository, _, token_service = make_admin_auth_service(admin)

    result = await service.admin_login(login_identifier=" root-admin ", password="correct-password")
    access_claims = token_service.validate_token(result["access_token"], expected_type="access")
    refresh_claims = token_service.validate_token(result["refresh_token"], expected_type="refresh")

    assert repository.username_lookups == ["root-admin"]
    assert access_claims["sub"] == admin.id
    assert access_claims["user_id"] == admin.id
    assert access_claims["identity_type"] == "admin"
    assert access_claims["role"] == "admin"
    assert refresh_claims["identity_type"] == "admin"
    assert result["admin"]["id"] == admin.id
    assert "password_hash" not in result["admin"]
    assert "two_factor_secret" not in result["admin"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admin_changes",
    [
        {"is_active": False},
        {"status": "suspended"},
        {"status": "blocked"},
        {"locked_until": datetime.now(timezone.utc) + timedelta(minutes=5)},
        {"role": "support"},
    ],
)
async def test_admin_login_rejects_unavailable_or_unconfigured_admin(admin_changes: dict[str, object]) -> None:
    admin = make_admin()
    for field, value in admin_changes.items():
        setattr(admin, field, value)
    service, _, _, _ = make_admin_auth_service(admin)

    with pytest.raises(AuthenticationException, match="Invalid credentials"):
        await service.admin_login(login_identifier="admin@example.com", password="correct-password")


@pytest.mark.asyncio
async def test_admin_login_uses_generic_error_for_nonexistent_account() -> None:
    service, _, _, _ = make_admin_auth_service(None)
    with pytest.raises(AuthenticationException, match="Invalid credentials"):
        await service.admin_login(login_identifier="missing@example.test", password="correct-password")


@pytest.mark.asyncio
async def test_admin_failed_login_tracks_attempts_and_locks_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_login_attempts", 2)
    monkeypatch.setattr(settings, "account_lock_duration_minutes", 15)
    admin = make_admin()
    service, _, _, _ = make_admin_auth_service(admin)

    for _ in range(2):
        with pytest.raises(AuthenticationException, match="Invalid credentials"):
            await service.admin_login(login_identifier="admin@example.com", password="wrong-password")

    assert admin.failed_login_attempts == 2
    assert admin.locked_until is not None
    with pytest.raises(AuthenticationException, match="Invalid credentials"):
        await service.admin_login(login_identifier="admin@example.com", password="correct-password")


@pytest.mark.asyncio
async def test_admin_mfa_must_complete_before_tokens_are_issued() -> None:
    admin = make_admin()
    admin.mfa_enabled = True
    otp_service = FakeOtpService()
    otp_service.admin_id = admin.id
    service, _, _, token_service = make_admin_auth_service(admin, mfa_service=otp_service)

    challenge = await service.admin_login(login_identifier="admin@example.com", password="correct-password")
    assert challenge["requires_mfa"] is True
    assert "access_token" not in challenge
    assert otp_service.sent["user_id"] == admin.id
    assert otp_service.sent["metadata"]["identity_type"] == "admin"

    result = await service.verify_admin_mfa(reference_id="mfa-reference", otp_code="456123")
    claims = token_service.validate_token(result["access_token"], expected_type="access")
    assert claims["identity_type"] == "admin"
    assert claims["sub"] == admin.id


@pytest.mark.asyncio
async def test_admin_refresh_rotates_without_converting_identity_and_rejects_old_token() -> None:
    admin = make_admin()
    service, _, _, token_service = make_admin_auth_service(admin)
    login = await service.admin_login(login_identifier="admin@example.com", password="correct-password")

    refreshed = await service.refresh_admin_token(refresh_token=login["refresh_token"])
    claims = token_service.validate_token(refreshed["refresh_token"], expected_type="refresh")
    assert claims["identity_type"] == "admin"
    assert claims["sub"] == admin.id
    assert claims["role"] == "admin"

    with pytest.raises(AuthenticationException):
        await service.refresh_admin_token(refresh_token=login["refresh_token"])


@pytest.mark.asyncio
async def test_user_refresh_token_cannot_become_admin_refresh() -> None:
    service, _, _, token_service = make_admin_auth_service(make_admin())
    user_refresh = token_service.create_refresh_token("22222222-2222-4222-8222-222222222222")

    with pytest.raises(AuthenticationException, match="invalid"):
        await service.refresh_admin_token(refresh_token=user_refresh)


@pytest.mark.asyncio
async def test_admin_refresh_rejects_wrong_type_expired_and_revoked_tokens() -> None:
    admin = make_admin()
    service, _, _, token_service = make_admin_auth_service(admin)
    identity_claims = {
        "identity_type": "admin",
        "user_id": admin.id,
        "role": "admin",
        "email": admin.email,
    }
    wrong_type_token = token_service.create_access_token(admin.id, extra_claims=identity_claims)
    expired_token = token_service.create_refresh_token(
        admin.id,
        extra_claims=identity_claims,
        family_id="expired-family",
        ttl=timedelta(seconds=-1),
    )
    with pytest.raises(AuthenticationException):
        await service.refresh_admin_token(refresh_token=wrong_type_token)
    with pytest.raises(AuthenticationException):
        await service.refresh_admin_token(refresh_token=expired_token)

    login = await service.admin_login(login_identifier="admin@example.com", password="correct-password")
    await token_service.revoke_token(login["refresh_token"], reason="test")
    with pytest.raises(AuthenticationException, match="revoked"):
        await service.refresh_admin_token(refresh_token=login["refresh_token"])


@pytest.mark.asyncio
async def test_admin_logout_revokes_tokens_session_and_refresh_family() -> None:
    admin = make_admin()
    service, _, _, token_service = make_admin_auth_service(admin)
    login = await service.admin_login(login_identifier="admin@example.com", password="correct-password")
    access_claims = token_service.validate_token(login["access_token"], expected_type="access")

    result = await service.logout_admin(access_token=login["access_token"], refresh_token=login["refresh_token"])

    assert result["message"] == "Logged out successfully."
    assert await token_service.check_revoked_token(login["access_token"]) is True
    assert await token_service.check_revoked_token(login["refresh_token"]) is True
    assert await token_service.check_revoked_token_family(access_claims["family_id"]) is True
    with pytest.raises(AuthenticationException):
        await service.refresh_admin_token(refresh_token=login["refresh_token"])


@pytest.mark.asyncio
async def test_admin_profile_uses_safe_response_and_revalidates_role() -> None:
    admin = make_admin()
    repository = FakeAdminRepository(admin)
    service = object.__new__(AdminService)
    service.admin_repository = repository

    profile = await service.get_admin_profile(admin_id=admin.id, token_role="admin")
    assert profile["id"] == admin.id
    assert "password_hash" not in profile
    assert "two_factor_secret" not in profile

    with pytest.raises(AuthenticationException):
        await service.get_admin_profile(admin_id=admin.id, token_role="super_admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_changes", [{"is_active": False}, {"status": "suspended"}])
async def test_admin_profile_rejects_inactive_admin(profile_changes: dict[str, object]) -> None:
    admin = make_admin()
    for field, value in profile_changes.items():
        setattr(admin, field, value)
    service = object.__new__(AdminService)
    service.admin_repository = FakeAdminRepository(admin)

    with pytest.raises(AuthenticationException):
        await service.get_admin_profile(admin_id=admin.id, token_role="admin")


@pytest.mark.asyncio
async def test_admin_profile_rejects_missing_admin() -> None:
    service = object.__new__(AdminService)
    service.admin_repository = FakeAdminRepository(None)

    with pytest.raises(AuthenticationException):
        await service.get_admin_profile(
            admin_id=UUID("11111111-1111-4111-8111-111111111111"),
            token_role="admin",
        )


@pytest.mark.asyncio
async def test_admin_logout_rejects_invalid_tokens() -> None:
    service, _, _, _ = make_admin_auth_service(make_admin())
    with pytest.raises(AuthenticationException):
        await service.logout_admin(access_token="invalid", refresh_token="invalid")


def test_auth_middleware_is_outer_to_admin_middleware() -> None:
    app = Starlette()
    app.add_middleware(AdminMiddleware, allowed_roles=["admin"])
    app.add_middleware(AuthMiddleware, public_paths=[])
    middleware_names = [getattr(middleware.cls, "__name__", "") for middleware in app.user_middleware]
    assert middleware_names.index("AuthMiddleware") < middleware_names.index("AdminMiddleware")


@pytest.mark.asyncio
async def test_admin_middleware_receives_authenticated_admin_context(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = {
        "sub": "admin-uuid",
        "user_id": "admin-uuid",
        "email": "admin@example.com",
        "role": "admin",
        "identity_type": "admin",
        "type": "access",
        "exp": 2_000_000_000,
    }

    class FakeTokenService:
        def validate_token(self, token: str, *, expected_type: str) -> dict[str, object]:
            assert token == "valid-token"
            assert expected_type == "access"
            return claims

        async def check_revoked_token(self, *, token: str, fail_closed: bool = False) -> bool:
            return False

    monkeypatch.setattr("app.middleware.auth_middleware.TokenService", FakeTokenService)
    auth = AuthMiddleware(lambda *_: None, public_paths=[])
    admin = AdminMiddleware(lambda *_: None, allowed_roles=["admin"])
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/me",
        "raw_path": b"/admin/me",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer valid-token")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)

    async def endpoint(_: Request) -> Response:
        return Response("ok", status_code=200)

    async def admin_dispatch(authenticated_request: Request) -> Response:
        assert authenticated_request.state.user.identity_type == "admin"
        assert authenticated_request.state.user.user_id == "admin-uuid"
        return await admin.dispatch(authenticated_request, endpoint)

    response = await auth.dispatch(request, admin_dispatch)
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("identity_type", "role", "expected_status"),
    [("user", "admin", 403), ("admin", "customer", 403), ("admin", "admin", 200)],
)
@pytest.mark.asyncio
async def test_admin_middleware_requires_admin_identity_and_allowed_role(
    identity_type: str,
    role: str,
    expected_status: int,
) -> None:
    middleware = AdminMiddleware(lambda *_: None, allowed_roles=["admin"])
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/me",
        "raw_path": b"/admin/me",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "http_version": "1.1",
        "state": {"user": SimpleNamespace(identity_type=identity_type, role=role)},
    }
    request = Request(scope)

    async def endpoint(_: Request) -> Response:
        return Response("ok", status_code=200)

    response = await middleware.dispatch(request, endpoint)
    assert response.status_code == expected_status