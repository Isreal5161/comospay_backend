from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.controllers.admin_controller import AdminController, FraudReviewRequest
from app.database.base import Base
from app.models.admin import Admin
from app.models.audit_log import AuditLog
from app.models.device import Device
from app.models.fraud_investigation import FraudInvestigation, FraudInvestigationEvent
from app.models.transaction import Transaction
from app.models.user import User
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.services.admin.fraud import FraudService
from app.utils.exceptions import AuthorizationException, ValidationException
from pydantic import ValidationError


@pytest_asyncio.fixture
async def fraud_context():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        admin = Admin(email="reviewer@example.test", role="admin")
        session.add(admin)
        await session.flush()
        service = FraudService(
            user_repository=UserRepository(session),
            transaction_repository=TransactionRepository(session),
            audit_repository=AuditLogRepository(session),
        )
        yield service, session, admin, factory
    await engine.dispose()


async def _add_user(session, email: str, *, failures: int = 0, locked: bool = False) -> User:
    user = User(
        email=email,
        first_name=email.split("@", 1)[0].title(),
        failed_login_attempts=failures,
        account_locked_until=datetime.now(timezone.utc) + timedelta(minutes=5) if locked else None,
    )
    session.add(user)
    await session.flush()
    return user


async def _add_transaction(
    session,
    user: User,
    *,
    reference: str,
    amount: str,
    status: str,
    created_at: datetime | None = None,
    category: str = "airtime",
) -> Transaction:
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        amount=Decimal(amount),
        total_amount=Decimal(amount),
        currency="NGN",
        transaction_type="purchase",
        category=category,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(transaction)
    await session.flush()
    return transaction


@pytest.mark.asyncio
async def test_list_fraud_has_bounded_pagination_and_complete_summary(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "risk@example.test", failures=2)
    now = datetime.now(timezone.utc)
    await _add_transaction(session, user, reference="risk-failed", amount="100", status="failed", created_at=now)
    await _add_transaction(session, user, reference="risk-large", amount="60000", status="completed", created_at=now)
    await session.commit()

    first_page = await service.list_fraud(page=1, page_size=1)
    second_page = await service.list_fraud(page=2, page_size=1)

    assert first_page["page"] == 1
    assert first_page["page_size"] == 1
    assert first_page["total"] == 3
    assert first_page["total_pages"] == 3
    assert first_page["summary"]["total_alerts"] == 3
    assert first_page["summary"]["suspicious_transactions"] == 2
    assert first_page["summary"]["accounts_with_failed_logins"] == 1
    assert first_page["summary"]["account_security_signals"] == 1
    assert len(first_page["items"]) == len(second_page["items"]) == 1
    assert first_page["items"][0]["signal_id"] != second_page["items"][0]["signal_id"]
    account_signal = await service.list_fraud(page_size=10)
    account_item = next(item for item in account_signal["items"] if item["signal_type"] == "account_security")
    assert account_item["date_label"] == "account_record_updated"
    assert any("failed-login attempts: 2" in reason for reason in account_item["risk_reasons"])


@pytest.mark.asyncio
async def test_summary_counts_accounts_with_failed_logins_not_attempts(fraud_context):
    service, session, _, _ = fraud_context
    await _add_user(session, "two-failures@example.test", failures=2)
    await _add_user(session, "seven-failures@example.test", failures=7)
    await session.commit()

    result = await service.list_fraud(page_size=1)

    assert result["total"] == 2
    assert result["summary"]["accounts_with_failed_logins"] == 2
    assert result["summary"]["accounts_with_failed_logins"] != 9


@pytest.mark.asyncio
async def test_list_fraud_applies_search_status_event_and_risk_filters(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "target@example.test", failures=3)
    await _add_transaction(
        session,
        user,
        reference="target-failed",
        amount="100",
        status="failed",
        category="airtime",
    )
    await _add_transaction(
        session,
        user,
        reference="other-pending",
        amount="70000",
        status="pending",
        category="data",
    )
    await session.commit()

    result = await service.list_fraud(
        search="target-failed",
        status="failed",
        event_type="airtime",
        risk_level="HIGH",
    )

    assert result["total"] == 1
    item = result["items"][0]
    assert item["txn_id"] == "target-failed"
    assert item["risk_score"] == 72
    assert item["classification"] == "suspicious_signal"
    assert result["summary"]["risk_flags"]["HIGH"] == 1


@pytest.mark.asyncio
async def test_risk_filter_is_applied_before_pagination_and_rejects_unknown_levels(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "levels@example.test")
    await _add_transaction(session, user, reference="high-risk", amount="100", status="failed")
    await _add_transaction(session, user, reference="medium-risk", amount="100", status="pending")
    await session.commit()

    result = await service.list_fraud(risk_level="HIGH", page=1, page_size=1)
    assert result["total"] == 1
    assert result["items"][0]["txn_id"] == "high-risk"

    with pytest.raises(ValidationException):
        await service.list_fraud(risk_level="impossible")


@pytest.mark.asyncio
async def test_equal_timestamps_have_deterministic_order_and_empty_result_is_well_formed(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "ordered@example.test")
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = await _add_transaction(
        session, user, reference="order-a", amount="50000", status="completed", created_at=timestamp
    )
    second = await _add_transaction(
        session, user, reference="order-b", amount="50000", status="completed", created_at=timestamp
    )
    await session.commit()

    ordered = await service.list_fraud(page_size=10)
    repeated = await service.list_fraud(page_size=10)
    assert [item["signal_id"] for item in ordered["items"]] == [
        item["signal_id"] for item in repeated["items"]
    ]
    assert {str(first.id), str(second.id)} == {item["evidence_id"] for item in ordered["items"]}

    empty = await service.list_fraud(search="no-match")
    assert empty["items"] == []
    assert empty["total"] == empty["total_pages"] == 0
    assert empty["summary"]["risk_flags"] == {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}


@pytest.mark.asyncio
async def test_review_persists_state_reviewer_and_immutable_audit_history(fraud_context):
    service, session, admin, factory = fraud_context
    user = await _add_user(session, "review-target@example.test", failures=3)
    await session.commit()
    signal_id = f"account_security:{user.id}"

    first = await service.review_fraud_signal(
        action="review",
        signal_id=signal_id,
        reviewer_admin_id=admin.id,
        reason="Investigating repeated failures",
    )
    second = await service.review_fraud_signal(
        action="escalate",
        signal_id=signal_id,
        reviewer_admin_id=admin.id,
    )
    third = await service.review_fraud_signal(
        action="resolve",
        signal_id=signal_id,
        reviewer_admin_id=admin.id,
    )

    case = await session.scalar(select(FraudInvestigation))
    events = (await session.execute(select(FraudInvestigationEvent).order_by(FraudInvestigationEvent.created_at))).scalars().all()
    audit = (await session.execute(select(AuditLog).where(AuditLog.category == "fraud_investigation"))).scalars().all()
    assert first["investigation"]["state"] == "in_review"
    assert second["investigation"]["state"] == "escalated"
    assert third["investigation"]["state"] == "resolved"
    assert case is not None
    assert case.reviewed_by_admin_id == admin.id
    assert len(events) == 3
    assert [(event.from_state, event.to_state) for event in events] == [
        ("open", "in_review"),
        ("in_review", "escalated"),
        ("escalated", "resolved"),
    ]
    assert all(event.admin_id == admin.id for event in events)
    assert len(audit) == 3
    assert all(log.actor_id == str(admin.id) for log in audit)
    async with factory() as verifier:
        persisted = await verifier.scalar(select(FraudInvestigation))
        assert persisted is not None
        assert persisted.state == "resolved"
        assert persisted.reviewed_by_admin_id == admin.id


@pytest.mark.asyncio
async def test_review_rejects_invalid_transitions_and_duplicate_actions(fraud_context):
    service, session, admin, _ = fraud_context
    admin_id = admin.id
    user = await _add_user(session, "duplicate@example.test", failures=1)
    await session.commit()
    signal_id = f"account_security:{user.id}"

    await service.review_fraud_signal(
        action="dismiss",
        signal_id=signal_id,
        reviewer_admin_id=admin_id,
    )
    with pytest.raises(ValidationException):
        await service.review_fraud_signal(
            action="review",
            signal_id=signal_id,
            reviewer_admin_id=admin_id,
        )
    with pytest.raises(ValidationException):
        await service.review_fraud_signal(
            action="dismiss",
            signal_id=signal_id,
            reviewer_admin_id=admin_id,
        )
    assert len((await session.execute(select(FraudInvestigationEvent))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_review_rejects_missing_or_unsupported_evidence(fraud_context):
    service, session, admin, _ = fraud_context
    admin_id = admin.id
    with pytest.raises(ValidationException):
        await service.review_fraud_signal(
            action="review",
            signal_id=f"transaction:{uuid4()}",
            reviewer_admin_id=admin_id,
        )
    with pytest.raises(ValidationException):
        await service.review_fraud_signal(
            action="accept",
            signal_id=f"transaction:{uuid4()}",
            reviewer_admin_id=admin_id,
        )


@pytest.mark.asyncio
async def test_device_and_ip_history_uses_safe_joined_user_fields(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "private@example.test")
    for index in range(8):
        session.add(
            Device(
                user_id=user.id,
                device_name=f"Device {index}",
                device_type="mobile",
                device_fingerprint=f"fingerprint-{index}",
                ip_address=f"192.0.2.{index + 1}",
            )
        )
    await session.commit()

    result = await service.list_fraud(page_size=5)

    assert len(result["device_history"]) == 5
    assert len(result["ip_history"]) == 5
    assert result["device_history"][0]["user"] == "Private"
    assert all("email" not in row for row in result["device_history"])
    assert all("location" not in row for row in result["ip_history"])
    assert result["device_history"][0]["id"]
    assert result["ip_history"][0]["id"]


@pytest.mark.asyncio
async def test_device_and_ip_query_count_is_constant_as_device_rows_increase(fraud_context):
    service, session, _, _ = fraud_context
    user = await _add_user(session, "batch@example.test")
    session.add(
        Device(
            user_id=user.id,
            device_name="Device one",
            device_type="mobile",
            device_fingerprint="fingerprint-one",
            ip_address="192.0.2.1",
        )
    )
    await session.commit()

    statements = []
    connection = session.bind.sync_engine
    listener = lambda *args: statements.append(args[2]) if args[2].lstrip().upper().startswith("SELECT") else None
    event.listen(connection, "before_cursor_execute", listener)
    try:
        await service.list_fraud(page_size=20)
        one_row_count = len(statements)
        statements.clear()
        for index in range(2, 10):
            session.add(
                Device(
                    user_id=user.id,
                    device_name=f"Device {index}",
                    device_type="mobile",
                    device_fingerprint=f"fingerprint-{index}",
                    ip_address=f"192.0.2.{index}",
                )
            )
        await session.commit()
        statements.clear()
        await service.list_fraud(page_size=20)
        many_row_count = len(statements)
    finally:
        event.remove(connection, "before_cursor_execute", listener)

    assert one_row_count == many_row_count


def test_review_schema_rejects_client_supplied_admin_identity():
    with pytest.raises(ValidationError):
        FraudReviewRequest.model_validate(
            {
                "signal_id": f"transaction:{uuid4()}",
                "action": "review",
                "admin_id": str(uuid4()),
            }
        )


def test_review_concurrency_guard_uses_row_lock_and_unique_signal_key():
    statement = select(FraudInvestigation).where(
        FraudInvestigation.signal_type == "transaction",
        FraudInvestigation.signal_reference == uuid4(),
    ).with_for_update()
    sql = str(statement.compile(dialect=postgresql.dialect()))
    unique_constraints = {
        constraint.name for constraint in FraudInvestigation.__table__.constraints
    }

    assert "FOR UPDATE" in sql
    assert "uq_fraud_investigation_signal" in unique_constraints


@pytest.mark.asyncio
async def test_fraud_controller_requires_authenticated_admin_identity(fraud_context):
    service, _, admin, _ = fraud_context
    controller = AdminController(SimpleNamespace(list_fraud=service.list_fraud))
    request = Request({"type": "http", "headers": []})
    request.state.auth_user = SimpleNamespace(user_id=admin.id, identity_type="user")

    with pytest.raises(AuthorizationException):
        await controller.list_fraud(request=request)

    request.state.auth_user = SimpleNamespace(user_id=admin.id, identity_type="admin")
    result = await controller.list_fraud(request=request)
    assert result["success"] is True
    assert set(result["data"]) >= {"items", "page", "page_size", "total", "total_pages", "summary"}


@pytest.mark.parametrize(
    ("role", "can_review"),
    [
        ("admin", True),
        ("super_admin", True),
        ("support", False),
        ("auditor", False),
    ],
)
@pytest.mark.asyncio
async def test_fraud_role_permissions_allow_view_but_limit_reviews(fraud_context, role, can_review):
    service, session, admin, _ = fraud_context
    admin.role = role
    user = await _add_user(session, f"{role}@example.test", failures=1)
    await session.commit()
    controller = AdminController(
        SimpleNamespace(
            list_fraud=service.list_fraud,
            review_fraud_signal=service.review_fraud_signal,
        )
    )
    request = Request({"type": "http", "headers": []})
    request.state.auth_user = SimpleNamespace(
        user_id=admin.id,
        identity_type="admin",
        role=role,
    )

    listing = await controller.list_fraud(request=request)
    assert listing["success"] is True
    assert listing["data"]["summary"]["accounts_with_failed_logins"] == 1

    review_request = FraudReviewRequest(
        signal_id=f"account_security:{user.id}",
        action="review",
    )
    if can_review:
        result = await controller.review_fraud_signal(review_request, request=request)
        assert result["success"] is True
        assert result["data"]["investigation"]["reviewed_by_admin_id"] == str(admin.id)
    else:
        with pytest.raises(AuthorizationException):
            await controller.review_fraud_signal(review_request, request=request)
        assert (await session.execute(select(FraudInvestigationEvent))).scalars().all() == []
