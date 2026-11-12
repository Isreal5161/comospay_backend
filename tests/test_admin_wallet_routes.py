from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.controllers.admin_controller import AdminController
from app.database.base import Base
from app.models.audit_log import AuditLog
from app.models.ledger import Ledger
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.routes import admin_routes
from app.services.admin.wallet import WalletAdministrationService
from app.services.ledger_service import LedgerService
from app.services.wallet.wallet_balance import WalletBalanceService
ADMIN_ID = UUID("11111111-1111-4111-8111-111111111111")


class RouteAdminRepository:
    def __init__(self, *, active: bool = True, status: str = "active", role: str = "admin") -> None:
        self.admin = SimpleNamespace(is_active=active, status=status, role=role)

    async def get_by_id(self, admin_id: UUID):
        return self.admin if admin_id == ADMIN_ID else None


@pytest_asyncio.fixture
async def route_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def build_route_app(
    session,
    *,
    active_admin: bool = True,
    admin_status: str = "active",
    admin_role: str = "admin",
):
    wallet_repository = WalletRepository(session)
    ledger_repository = LedgerRepository(session)
    ledger_service = LedgerService(
        ledger_repository=ledger_repository,
        wallet_repository=wallet_repository,
        user_repository=UserRepository(session),
    )
    balance_service = WalletBalanceService(
        wallet_repository=wallet_repository,
        ledger_service=ledger_service,
        session=session,
    )
    wallet_service = WalletAdministrationService(
        wallet_repository=wallet_repository,
        ledger_repository=ledger_repository,
        admin_repository=RouteAdminRepository(
            active=active_admin,
            status=admin_status,
            role=admin_role,
        ),
        audit_repository=AuditLogRepository(session),
        wallet_balance_service=balance_service,
        session=session,
    )
    controller = AdminController(SimpleNamespace(
        list_wallets=wallet_service.list_wallets,
        search_wallets=wallet_service.search_wallets,
        adjust_wallet=wallet_service.adjust_wallet,
        get_user_ledger=wallet_service.get_user_ledger,
        get_user_ledger_by_reference=wallet_service.get_user_ledger_by_reference,
    ))
    app = FastAPI()
    app.include_router(admin_routes.router)

    @app.middleware("http")
    async def attach_test_admin_context(request, call_next):
        raw_admin_id = request.headers.get("x-test-admin-id")
        if raw_admin_id:
            request.state.auth_user = SimpleNamespace(user_id=raw_admin_id)
        return await call_next(request)

    async def override_admin_controller():
        return controller

    app.dependency_overrides[admin_routes.get_admin_controller] = override_admin_controller
    return app, ledger_service


async def add_route_wallet(
    session,
    *,
    email: str = "wallet-customer@example.test",
    phone: str = "+15555550100",
    wallet_reference: str = "wallet-route-test",
    currency: str = "NGN",
):
    user = User(
        first_name="Wallet",
        last_name="Customer",
        email=email,
        phone=phone,
        password_hash="test-hash",
        status="active",
    )
    session.add(user)
    await session.flush()
    wallet = Wallet(
        user_id=user.id,
        wallet_reference=wallet_reference,
        currency=currency,
        available_balance=Decimal("100.00"),
        ledger_balance=Decimal("100.00"),
        locked_balance=Decimal("0.00"),
    )
    session.add(wallet)
    await session.commit()
    return user, wallet


async def add_route_ledger_entry(
    ledger_service,
    *,
    user_id: UUID,
    wallet_id: UUID,
    reference: str,
    transaction_type: str = "ADMIN_CREDIT",
    amount: Decimal = Decimal("12.50"),
    currency: str = "NGN",
    reason: str = "Route reconciliation test",
    created_by: str = f"admin:{ADMIN_ID}",
    status: str = "POSTED",
    created_at: datetime | None = None,
):
    is_credit = transaction_type == "ADMIN_CREDIT"
    entry = await ledger_service.create_entry(
        wallet_id=wallet_id,
        user_id=user_id,
        transaction_reference=reference,
        transaction_type=transaction_type,
        entry_type="CREDIT" if is_credit else "DEBIT",
        opening_balance=Decimal("100.00"),
        debit_amount=Decimal("0.00") if is_credit else amount,
        credit_amount=amount if is_credit else Decimal("0.00"),
        currency=currency,
        description=reason,
        created_by=created_by,
        status=status,
        metadata={"reason": reason, "admin_id": str(ADMIN_ID)},
    )
    if created_at is not None:
        entry.created_at = created_at
        entry.updated_at = created_at
        await ledger_service.ledger_repository.session.flush()
    return entry


def adjustment_payload(user_id: UUID, wallet_id: UUID) -> dict[str, str]:
    return {
        "user_id": str(user_id),
        "wallet_id": str(wallet_id),
        "amount": "12.50",
        "currency": "NGN",
        "reason": "Route authorization test",
        "reference": "route-authorization-test",
    }


@pytest.mark.asyncio
async def test_admin_wallet_routes_reject_missing_authenticated_context(route_session):
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/admin/wallets")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_adjust_admin_wallet_route_rejects_missing_authenticated_context(route_session):
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/wallets/adjust",
            json=adjustment_payload(ADMIN_ID, ADMIN_ID),
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_adjust_admin_wallet_route_rejects_inactive_admin(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session, active_admin=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/wallets/adjust",
            headers={"x-test-admin-id": str(ADMIN_ID)},
            json=adjustment_payload(user.id, wallet.id),
        )

    assert response.status_code == 403
    saved_wallet = await route_session.get(Wallet, wallet.id)
    assert saved_wallet is not None and saved_wallet.available_balance == Decimal("100.00")
    assert await route_session.scalar(select(Ledger)) is None
    assert await route_session.scalar(select(AuditLog)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_admin", "admin_status", "admin_role"),
    [
        (False, "active", "admin"),
        (True, "disabled", "admin"),
        (True, "active", "viewer"),
    ],
)
async def test_admin_wallet_routes_reject_inactive_or_unauthorized_admins(
    route_session,
    active_admin,
    admin_status,
    admin_role,
):
    app, _ = build_route_app(
        route_session,
        active_admin=active_admin,
        admin_status=admin_status,
        admin_role=admin_role,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_admin_wallets_route_returns_authorized_database_records(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets?page=1&page_size=10&currency=NGN",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(wallet.id)
    assert data["items"][0]["user_id"] == str(user.id)
    assert data["items"][0]["user_name"] == "Wallet Customer"
    assert data["items"][0]["user_email"] == user.email
    assert data["items"][0]["available_balance"] == "100.00"


@pytest.mark.asyncio
async def test_get_admin_wallets_returns_valid_empty_page(route_session):
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets?page=1&page_size=10",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    data = response.json()["data"]["data"]
    assert data["items"] == []
    assert data["total"] == 0
    assert data["total_wallets"] == 0
    assert data["currencies"] == []


@pytest.mark.asyncio
async def test_get_admin_wallets_paginates_multiple_records_deterministically(route_session):
    _, first_wallet = await add_route_wallet(
        route_session,
        email="wallet-page-one@example.test",
        phone="+15555550101",
        wallet_reference="wallet-page-one",
    )
    _, second_wallet = await add_route_wallet(
        route_session,
        email="wallet-page-two@example.test",
        phone="+15555550102",
        wallet_reference="wallet-page-two",
    )
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first_wallet.created_at = timestamp
    second_wallet.created_at = timestamp
    await route_session.commit()
    app, _ = build_route_app(route_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        page_one = await client.get(
            "/admin/wallets?page=1&page_size=1",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )
        repeated_page_one = await client.get(
            "/admin/wallets?page=1&page_size=1",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )
        page_two = await client.get(
            "/admin/wallets?page=2&page_size=1",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert page_one.status_code == repeated_page_one.status_code == page_two.status_code == 200
    first_data = page_one.json()["data"]["data"]
    repeated_data = repeated_page_one.json()["data"]["data"]
    second_data = page_two.json()["data"]["data"]
    assert first_data["total"] == second_data["total"] == 2
    assert first_data["items"][0]["id"] == repeated_data["items"][0]["id"]
    assert first_data["items"][0]["id"] != second_data["items"][0]["id"]
    assert {first_data["items"][0]["id"], second_data["items"][0]["id"]} == {
        str(first_wallet.id),
        str(second_wallet.id),
    }


@pytest.mark.asyncio
async def test_get_admin_wallets_handles_missing_optional_user_relationship(route_session):
    wallet = Wallet(
        user_id=UUID("22222222-2222-4222-8222-222222222222"),
        wallet_reference="wallet-without-user",
        currency="NGN",
        available_balance=Decimal("0.00"),
        ledger_balance=Decimal("0.00"),
        locked_balance=Decimal("0.00"),
    )
    route_session.add(wallet)
    await route_session.commit()
    app, _ = build_route_app(route_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets?page=1&page_size=10",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    item = response.json()["data"]["data"]["items"][0]
    assert item["id"] == str(wallet.id)
    assert item["user_name"] is None
    assert item["user_email"] is None
    assert item["user_phone"] is None
    assert item["available_balance"] == "0.00"


@pytest.mark.asyncio
async def test_get_admin_wallets_does_not_load_unneeded_sensitive_or_financial_relations(route_session):
    await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    statements = []
    connection = route_session.bind.sync_engine
    listener = lambda *args: statements.append(args[2]) if args[2].lstrip().upper().startswith("SELECT") else None
    event.listen(connection, "before_cursor_execute", listener)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/admin/wallets?page=1&page_size=10",
                headers={"x-test-admin-id": str(ADMIN_ID)},
            )
    finally:
        event.remove(connection, "before_cursor_execute", listener)

    assert response.status_code == 200
    sql = "\n".join(statements).lower()
    assert "password_hash" not in sql
    assert "transaction_pin_reference" not in sql
    assert "from ledgers" not in sql
    assert "from transactions" not in sql
    assert "from virtual_accounts" not in sql


@pytest.mark.asyncio
async def test_wallet_search_route_uses_supported_filters(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets/search",
            params={"query": user.email, "page": 1, "page_size": 10},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    data = response.json()["data"]["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(wallet.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 10), (1, 0), (1, 101)],
)
async def test_get_admin_wallets_validates_pagination_bounds(route_session, page, page_size):
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets",
            params={"page": page, "page_size": page_size},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_admin_wallets_rejects_unsupported_order_field(route_session):
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets",
            params={"order_by": "metadata_payload"},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_admin_wallets_does_not_expose_database_exception_details(route_session):
    async def fail_query(**kwargs):
        raise RuntimeError("sensitive database diagnostic")

    wallet_service = WalletAdministrationService(
        wallet_repository=SimpleNamespace(list_wallets=fail_query),
        admin_repository=RouteAdminRepository(),
        session=route_session,
    )
    controller = AdminController(SimpleNamespace(list_wallets=wallet_service.list_wallets))
    app = FastAPI()
    app.include_router(admin_routes.router)

    @app.middleware("http")
    async def attach_test_admin_context(request, call_next):
        request.state.auth_user = SimpleNamespace(user_id=str(ADMIN_ID))
        return await call_next(request)

    async def override_admin_controller():
        return controller

    app.dependency_overrides[admin_routes.get_admin_controller] = override_admin_controller

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/admin/wallets?page=1&page_size=10")

    assert response.status_code == 500
    assert response.json()["detail"] == "An unexpected error occurred while processing the request."
    assert "sensitive database diagnostic" not in response.text


@pytest.mark.asyncio
async def test_get_admin_wallets_searches_wallet_and_user_identifiers(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    search_terms = (
        str(wallet.id),
        str(user.id),
        wallet.wallet_reference,
        "Wallet Customer",
        user.email,
        user.phone,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for term in search_terms:
            response = await client.get(
                "/admin/wallets",
                params={"query": term},
                headers={"x-test-admin-id": str(ADMIN_ID)},
            )
            assert response.status_code == 200
            data = response.json()["data"]["data"]
            assert data["total"] == 1, f"Wallet search did not match {term!r}"
            assert data["items"][0]["id"] == str(wallet.id)


@pytest.mark.asyncio
async def test_get_admin_wallets_returns_global_currency_options_and_total(route_session):
    _, ngn_wallet = await add_route_wallet(route_session)
    await add_route_wallet(
        route_session,
        email="wallet-customer-usd@example.test",
        phone="+15555550101",
        wallet_reference="wallet-route-test-usd",
        currency="USD",
    )
    app, _ = build_route_app(route_session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/admin/wallets",
            params={"page": 1, "page_size": 1, "currency": "NGN"},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    data = response.json()["data"]["data"]
    assert data["total"] == 1
    assert data["total_wallets"] == 2
    assert data["currencies"] == ["NGN", "USD"]
    assert data["items"][0]["id"] == str(ngn_wallet.id)


@pytest.mark.asyncio
async def test_wallet_ledger_reference_route_rejects_unauthenticated_and_inactive_admins(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    endpoint = (
        f"/admin/users/{user.id}/wallets/{wallet.id}/ledger/reference"
        "?transaction_reference=restricted-reference"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        unauthenticated = await client.get(endpoint)

    assert unauthenticated.status_code == 401

    inactive_app, _ = build_route_app(route_session, active_admin=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=inactive_app),
        base_url="http://test",
    ) as client:
        inactive = await client.get(
            endpoint,
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert inactive.status_code == 403


@pytest.mark.asyncio
async def test_wallet_ledger_reference_route_finds_entry_older_than_first_ledger_page(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, ledger_service = build_route_app(route_session)
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    reference = "reconciliation-later-page"
    await add_route_ledger_entry(
        ledger_service,
        user_id=user.id,
        wallet_id=wallet.id,
        reference=reference,
        created_at=base_time,
    )
    for index in range(25):
        await add_route_ledger_entry(
            ledger_service,
            user_id=user.id,
            wallet_id=wallet.id,
            reference=f"later-ledger-entry-{index}",
            created_at=base_time + timedelta(seconds=index + 1),
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        page_one = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger?page=1&page_size=20",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )
        lookup = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger/reference",
            params={"transaction_reference": reference},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert page_one.status_code == 200
    assert all(
        entry["transaction_reference"] != reference
        for entry in page_one.json()["data"]["data"]["items"]
    )
    assert lookup.status_code == 200
    result = lookup.json()["data"]["data"]
    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["transaction_reference"] == reference
    assert result["items"][0]["status"] == "POSTED"
    assert result["items"][0]["transaction_type"] == "ADMIN_CREDIT"
    assert result["items"][0]["credit_amount"] == "12.50"
    assert result["items"][0]["debit_amount"] == "0.00"
    assert result["items"][0]["currency"] == "NGN"
    assert result["items"][0]["adjustment_reason"] == "Route reconciliation test"
    assert result["items"][0]["created_by"] == f"admin:{ADMIN_ID}"
    assert result["items"][0]["wallet_id"] == str(wallet.id)
    assert result["items"][0]["user_id"] == str(user.id)


@pytest.mark.asyncio
async def test_wallet_ledger_reference_route_counts_duplicates_across_ledger_pages(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, ledger_service = build_route_app(route_session)
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    reference = "duplicate-reference-across-pages"
    for index in range(25):
        await add_route_ledger_entry(
            ledger_service,
            user_id=user.id,
            wallet_id=wallet.id,
            reference=reference if index in {0, 24} else f"unrelated-{index}",
            created_at=base_time + timedelta(seconds=index),
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger/reference",
            params={"transaction_reference": reference},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    result = response.json()["data"]["data"]
    assert result["total"] == 2
    assert len(result["items"]) == 2
    assert {entry["transaction_reference"] for entry in result["items"]} == {reference}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "transaction_type", "amount", "currency", "reason", "created_by"),
    [
        ("PENDING", "ADMIN_CREDIT", Decimal("12.50"), "NGN", "Route reconciliation test", f"admin:{ADMIN_ID}"),
        ("FAILED", "ADMIN_CREDIT", Decimal("12.50"), "NGN", "Route reconciliation test", f"admin:{ADMIN_ID}"),
        ("POSTED", "ADMIN_DEBIT", Decimal("12.50"), "NGN", "Route reconciliation test", f"admin:{ADMIN_ID}"),
        ("POSTED", "ADMIN_CREDIT", Decimal("12.51"), "NGN", "Route reconciliation test", f"admin:{ADMIN_ID}"),
        ("POSTED", "ADMIN_CREDIT", Decimal("12.50"), "USD", "Route reconciliation test", f"admin:{ADMIN_ID}"),
        ("POSTED", "ADMIN_CREDIT", Decimal("12.50"), "NGN", "Different reason", f"admin:{ADMIN_ID}"),
        ("POSTED", "ADMIN_CREDIT", Decimal("12.50"), "NGN", "Route reconciliation test", "admin:22222222-2222-4222-8222-222222222222"),
    ],
)
async def test_wallet_ledger_reference_route_returns_nonmatching_records_for_unresolved_review(
    route_session,
    status,
    transaction_type,
    amount,
    currency,
    reason,
    created_by,
):
    user, wallet = await add_route_wallet(route_session)
    app, ledger_service = build_route_app(route_session)
    reference = "nonmatching-adjustment"
    await add_route_ledger_entry(
        ledger_service,
        user_id=user.id,
        wallet_id=wallet.id,
        reference=reference,
        status=status,
        transaction_type=transaction_type,
        amount=amount,
        currency=currency,
        reason=reason,
        created_by=created_by,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger/reference",
            params={"transaction_reference": reference},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    result = response.json()["data"]["data"]
    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == status
    assert result["items"][0]["transaction_type"] == transaction_type
    assert result["items"][0]["currency"] == currency
    assert result["items"][0]["adjustment_reason"] == reason
    assert result["items"][0]["created_by"] == created_by


@pytest.mark.asyncio
async def test_wallet_ledger_reference_route_does_not_match_another_wallet_or_user(route_session):
    user, wallet = await add_route_wallet(route_session)
    other_user, other_wallet = await add_route_wallet(
        route_session,
        email="other-customer@example.test",
        phone="+15555550199",
        wallet_reference="other-wallet-reference",
    )
    app, ledger_service = build_route_app(route_session)
    reference = "other-wallet-adjustment-reference"
    await add_route_ledger_entry(
        ledger_service,
        user_id=other_user.id,
        wallet_id=other_wallet.id,
        reference=reference,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        other_wallet_lookup = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger/reference",
            params={"transaction_reference": reference},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )
        mismatched_user_lookup = await client.get(
            f"/admin/users/{other_user.id}/wallets/{wallet.id}/ledger/reference",
            params={"transaction_reference": reference},
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert other_wallet_lookup.status_code == 200
    assert other_wallet_lookup.json()["data"]["data"] == {"total": 0, "items": []}
    assert mismatched_user_lookup.status_code == 422


@pytest.mark.asyncio
async def test_adjust_admin_wallet_route_uses_authenticated_admin_and_returns_contract(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/wallets/adjust",
            headers={"x-test-admin-id": str(ADMIN_ID)},
            json={
                "user_id": str(user.id),
                "wallet_id": str(wallet.id),
                "amount": "12.50",
                "currency": "NGN",
                "reason": "Route integration test",
                "reference": "route-adjustment-1",
            },
        )
        history_response = await client.get(
            f"/admin/users/{user.id}/wallets/{wallet.id}/ledger",
            headers={"x-test-admin-id": str(ADMIN_ID)},
        )

    assert response.status_code == 200
    adjustment = response.json()["data"]["data"]
    assert adjustment["operation"] == "manual_credit"
    assert adjustment["reference"] == "route-adjustment-1"
    assert adjustment["idempotent"] is False
    audit = await route_session.scalar(select(AuditLog))
    ledger = await route_session.scalar(select(Ledger))
    saved_wallet = await route_session.get(Wallet, wallet.id)
    assert audit is not None and audit.actor_id == str(ADMIN_ID)
    assert ledger is not None and ledger.created_by == f"admin:{ADMIN_ID}"
    assert saved_wallet is not None and saved_wallet.available_balance == Decimal("112.50")
    history = history_response.json()["data"]["data"]
    assert history["items"][0]["transaction_reference"] == "route-adjustment-1"
    assert history["items"][0]["created_by"] == f"admin:{ADMIN_ID}"
    assert history["items"][0]["adjustment_reason"] == "Route integration test"


@pytest.mark.asyncio
async def test_adjust_admin_wallet_route_rejects_invalid_payload(route_session):
    user, wallet = await add_route_wallet(route_session)
    app, _ = build_route_app(route_session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/wallets/adjust",
            headers={"x-test-admin-id": str(ADMIN_ID)},
            json={
                "user_id": str(user.id),
                "wallet_id": str(wallet.id),
                "amount": "10.00",
                "currency": "NGN",
                "reference": "missing-reason",
            },
        )

    assert response.status_code == 422
    assert (await route_session.get(Wallet, wallet.id)).available_balance == Decimal("100.00")
    assert await route_session.scalar(select(Ledger)) is None
    assert await route_session.scalar(select(AuditLog)) is None


@pytest.mark.asyncio
async def test_adjust_admin_wallet_route_rolls_back_all_changes_on_ledger_failure(route_session):
    user, wallet = await add_route_wallet(route_session)
    wallet_id = wallet.id
    app, ledger_service = build_route_app(route_session)

    async def fail_ledger_creation(**_kwargs):
        raise RuntimeError("simulated ledger persistence failure")

    ledger_service.create_entry = fail_ledger_creation
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/admin/wallets/adjust",
            headers={"x-test-admin-id": str(ADMIN_ID)},
            json={
                "user_id": str(user.id),
                "wallet_id": str(wallet_id),
                "amount": "12.50",
                "currency": "NGN",
                "reason": "Rollback route test",
                "reference": "route-adjustment-failure",
            },
        )

    assert response.status_code == 500
    saved_balance = await route_session.scalar(
        select(Wallet.available_balance).where(Wallet.id == wallet_id)
    )
    assert saved_balance == Decimal("100.00")
    assert await route_session.scalar(select(Ledger)) is None
    assert await route_session.scalar(select(AuditLog)) is None
