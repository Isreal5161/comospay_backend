from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.admin_schema import AdminUserDetailResponse, AdminUserListResponse
from app.services.account_state import UserAccountStateService
from app.services.admin.users import UserAdministrationService
from app.utils.exceptions import AuthorizationException, ValidationException
from app.utils.response import success_response


_ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeAdminRepository:
    def __init__(self, *, active: bool = True, status: str = "active", role: str = "admin") -> None:
        self.active = active
        self.status = status
        self.role = role

    async def get_by_id(self, admin_id: UUID):
        if admin_id != _ADMIN_ID:
            return None
        return type("Admin", (), {
            "is_active": self.active,
            "status": self.status,
            "role": self.role,
        })()


class FakeSessionService:
    def __init__(self) -> None:
        self.revocations: list[tuple[str, str]] = []

    async def revoke_all_user_sessions(self, *, user_id: str, reason: str) -> int:
        self.revocations.append((user_id, reason))
        return 2


@pytest_asyncio.fixture
async def user_admin_service():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = UserAdministrationService(
            user_repository=UserRepository(session),
            admin_repository=FakeAdminRepository(),
            account_state_service=UserAccountStateService(),
            session_service=FakeSessionService(),
            session=session,
        )
        yield service, session
    await engine.dispose()


def _user(
    *,
    email: str,
    first_name: str,
    last_name: str,
    username: str,
    phone: str,
    status: str = "active",
) -> User:
    return User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        username=username,
        phone=phone,
        status=status,
        password_hash="must-not-be-exposed",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_list_users_supports_database_search_status_pagination_sort_and_contract(user_admin_service):
    service, session = user_admin_service
    users = [
        _user(
            email="alice@example.test",
            first_name="Alice",
            last_name="Jones",
            username="alicej",
            phone="+234800000001",
            status="active",
        ),
        _user(
            email="bob@example.test",
            first_name="Bob",
            last_name="Smith",
            username="bsmith",
            phone="+234800000002",
            status="suspended",
        ),
        _user(
            email="carol@example.test",
            first_name="Carol",
            last_name="Jones",
            username="carolj",
            phone="+234800000003",
            status="active",
        ),
    ]
    session.add_all(users)
    await session.flush()

    for term in ("ALICE", "smith", "carolj", "+234800000003"):
        result = await service.list_users(admin_id=_ADMIN_ID, search=term)
        assert result["total"] == 1

    filtered = await service.list_users(
        admin_id=_ADMIN_ID,
        status="active",
        page=1,
        page_size=1,
        sort_by="first_name",
        sort_order="asc",
    )
    assert filtered["total"] == 2
    assert filtered["total_pages"] == 2
    assert filtered["page"] == 1
    assert filtered["page_size"] == 1
    assert filtered["items"][0]["first_name"] == "Alice"
    assert "password_hash" not in filtered["items"][0]
    assert "failed_login_attempts" not in filtered["items"][0]

    second_page = await service.list_users(
        admin_id=_ADMIN_ID,
        status="active",
        page=2,
        page_size=1,
        sort_by="first_name",
        sort_order="asc",
    )
    assert second_page["total"] == 2
    assert [item["first_name"] for item in second_page["items"]] == ["Carol"]

    envelope = success_response(data=filtered, message="Users retrieved successfully.")
    validated = AdminUserListResponse.model_validate(envelope)
    assert validated.data.total == 2
    assert validated.data.items[0].id == users[0].id
    assert validated.data.items[0].is_email_verified is False


@pytest.mark.asyncio
async def test_list_users_rejects_invalid_pagination_and_sort_fields(user_admin_service):
    service, _ = user_admin_service

    with pytest.raises(ValidationException, match="Unsupported user sort field"):
        await service.list_users(admin_id=_ADMIN_ID, sort_by="password_hash")
    with pytest.raises(ValidationException, match="Page size"):
        await service.list_users(admin_id=_ADMIN_ID, page_size=101)
    with pytest.raises(ValidationException, match="Page must"):
        await service.list_users(admin_id=_ADMIN_ID, page=0)


@pytest.mark.asyncio
async def test_get_user_returns_uuid_profile_and_unknown_user_uses_not_found_error(user_admin_service):
    service, session = user_admin_service
    user = _user(
        email="detail@example.test",
        first_name="Detail",
        last_name="User",
        username="detailuser",
        phone="+234800000004",
    )
    session.add(user)
    await session.flush()

    result = await service.get_user(admin_id=_ADMIN_ID, user_id=user.id)
    assert result["id"] == str(user.id)
    assert result["first_name"] == "Detail"
    assert "password_hash" not in result
    assert "account_locked_until" not in result
    assert AdminUserDetailResponse.model_validate(
        success_response(data=result, message="User retrieved successfully.")
    ).data.id == user.id

    with pytest.raises(ValidationException) as exc_info:
        await service.get_user(admin_id=_ADMIN_ID, user_id=uuid4())
    assert exc_info.value.error_code == "USER_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_status", "expected_active", "expected_blocked", "expected_suspended"),
    [
        ("suspend", "suspended", True, False, True),
        ("deactivate", "inactive", False, False, False),
        ("activate", "active", True, False, False),
        ("block", "blocked", True, True, False),
        ("unblock", "active", True, False, False),
    ],
)
async def test_manage_user_applies_supported_account_state_actions(
    user_admin_service,
    action,
    expected_status,
    expected_active,
    expected_blocked,
    expected_suspended,
):
    service, session = user_admin_service
    user = _user(
        email=f"{action}@example.test",
        first_name="Action",
        last_name="User",
        username=f"{action}user",
        phone="+234800000005",
    )
    session.add(user)
    await session.flush()

    result = await service.manage_user(
        admin_id=_ADMIN_ID,
        user_id=user.id,
        action=action,
        reason="reviewed by support",
    )
    assert result["action"] == action
    assert result["user"]["status"] == expected_status
    assert result["user"]["is_active"] is expected_active
    assert result["user"]["is_blocked"] is expected_blocked
    assert result["user"]["is_suspended"] is expected_suspended
    expected_revocations = 1 if action in {"deactivate", "suspend", "block"} else 0
    assert len(service.session_service.revocations) == expected_revocations
    if expected_revocations:
        assert service.session_service.revocations[0][1] == f"admin_user_{action}"


@pytest.mark.asyncio
async def test_reactivation_does_not_restore_sessions_revoked_by_deactivation(user_admin_service):
    service, session = user_admin_service
    user = _user(
        email="deactivation-cycle@example.test",
        first_name="Reactivation",
        last_name="User",
        username="reactivationuser",
        phone="+234800000007",
    )
    session.add(user)
    await session.flush()

    await service.manage_user(admin_id=_ADMIN_ID, user_id=user.id, action="deactivate")
    assert service.session_service.revocations == [(str(user.id), "admin_user_deactivate")]

    result = await service.manage_user(admin_id=_ADMIN_ID, user_id=user.id, action="activate")

    assert result["user"]["status"] == "active"
    assert service.session_service.revocations == [(str(user.id), "admin_user_deactivate")]


@pytest.mark.asyncio
async def test_manage_user_rejects_unsupported_action(user_admin_service):
    service, session = user_admin_service
    user = _user(
        email="unsupported@example.test",
        first_name="Unsupported",
        last_name="Action",
        username="unsupportedaction",
        phone="+234800000006",
    )
    session.add(user)
    await session.flush()

    with pytest.raises(ValidationException) as exc_info:
        await service.manage_user(admin_id=_ADMIN_ID, user_id=user.id, action="delete")
    assert exc_info.value.error_code == "USER_ACTION_INVALID"


@pytest.mark.asyncio
async def test_user_management_requires_an_active_admin(user_admin_service):
    service, _ = user_admin_service

    with pytest.raises(AuthorizationException):
        await service.list_users()

    service.admin_repository = FakeAdminRepository(active=False)
    with pytest.raises(AuthorizationException):
        await service.list_users(admin_id=_ADMIN_ID)

    service.admin_repository = FakeAdminRepository(status="suspended")
    with pytest.raises(AuthorizationException):
        await service.list_users(admin_id=_ADMIN_ID)

    service.admin_repository = FakeAdminRepository(role="customer")
    with pytest.raises(AuthorizationException):
        await service.list_users(admin_id=_ADMIN_ID)
