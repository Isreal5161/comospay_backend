from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.routes import admin_routes
from app.services.admin.sessions import SessionAdministrationService
from app.services.auth.session_service import SessionNotFoundException, SessionRevokedException, SessionService


ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get(self, key: str) -> object | None:
        return self.values.get(key)

    async def set(self, key: str, value: object, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def sadd(self, key: str, *members: object) -> int:
        values = self.values.setdefault(key, set())
        assert isinstance(values, set)
        previous_count = len(values)
        values.update(members)
        return len(values) - previous_count

    async def smembers(self, key: str) -> set[object]:
        values = self.values.get(key, set())
        assert isinstance(values, set)
        return set(values)

    async def srem(self, key: str, *members: object) -> int:
        values = self.values.get(key, set())
        assert isinstance(values, set)
        removed = sum(member in values for member in members)
        values.difference_update(members)
        return removed

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.values

    async def lpush(self, key: str, value: object) -> int:
        values = self.values.setdefault(key, [])
        assert isinstance(values, list)
        values.insert(0, value)
        return len(values)

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        values = self.values.get(key, [])
        assert isinstance(values, list)
        self.values[key] = values[start : stop + 1]
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[object]:
        values = self.values.get(key, [])
        assert isinstance(values, list)
        return values[start : stop + 1]


class ActiveAdminRepository:
    async def get_by_id(self, admin_id: UUID):
        if admin_id != ADMIN_ID:
            return None
        return type("Admin", (), {"is_active": True, "status": "active", "role": "admin"})()


@pytest.fixture
def session_admin_service():
    redis = FakeRedis()
    session_service = SessionService(redis_client=redis)
    admin_service = SessionAdministrationService(
        session_service=session_service,
        admin_repository=ActiveAdminRepository(),
    )
    return session_service, admin_service


@pytest.mark.asyncio
async def test_admin_session_list_returns_persistent_session_identifier(session_admin_service):
    session_service, admin_service = session_admin_service
    user_id = str(uuid4())
    created = await session_service.create_session(user_id=user_id)

    result = await admin_service.get_user_sessions(user_id=user_id, admin_id=ADMIN_ID)

    assert result["data"][0]["session_id"] == created["session_id"]


@pytest.mark.asyncio
async def test_admin_can_revoke_session_scoped_to_its_user(session_admin_service):
    session_service, admin_service = session_admin_service
    user_id = str(uuid4())
    created = await session_service.create_session(user_id=user_id)

    result = await admin_service.revoke_session_by_id(
        user_id=user_id,
        session_id=created["session_id"],
        admin_id=ADMIN_ID,
    )

    assert result["data"]["session_id"] == created["session_id"]
    assert result["data"]["status"] == "revoked"
    saved = await session_service.retrieve_session(session_id=created["session_id"], allow_inactive=True)
    assert saved["status"] == "revoked"
    with pytest.raises(SessionRevokedException):
        await session_service.validate_session(session_id=created["session_id"])


@pytest.mark.asyncio
async def test_admin_cannot_revoke_another_users_session_through_scoped_route(session_admin_service):
    session_service, admin_service = session_admin_service
    owner_id = str(uuid4())
    supplied_user_id = str(uuid4())
    created = await session_service.create_session(user_id=owner_id)

    with pytest.raises(SessionNotFoundException):
        await admin_service.revoke_session_by_id(
            user_id=supplied_user_id,
            session_id=created["session_id"],
            admin_id=ADMIN_ID,
        )

    saved = await session_service.retrieve_session(session_id=created["session_id"])
    assert saved["status"] == "active"


@pytest.mark.asyncio
async def test_admin_scoped_session_revocation_handles_retries_and_missing_ids(session_admin_service):
    session_service, admin_service = session_admin_service
    user_id = str(uuid4())
    created = await session_service.create_session(user_id=user_id)
    arguments = {
        "user_id": user_id,
        "session_id": created["session_id"],
        "admin_id": ADMIN_ID,
    }

    first = await admin_service.revoke_session_by_id(**arguments)
    retry = await admin_service.revoke_session_by_id(**arguments)

    assert first["data"]["idempotent"] is False
    assert retry["data"]["idempotent"] is True
    with pytest.raises(SessionNotFoundException):
        await admin_service.revoke_session_by_id(
            user_id=user_id,
            session_id="does-not-exist",
            admin_id=ADMIN_ID,
        )


def test_admin_scoped_session_revoke_route_is_registered():
    paths = {(route.path, method) for route in admin_routes.router.routes for method in route.methods or []}
    assert ("/admin/users/{user_id}/sessions/{session_id}/revoke", "POST") in paths
