from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.admin import Admin
from app.models.support import SupportMessage, SupportTicket
from app.models.user import User
from app.repositories.admin_repository import AdminRepository
from app.repositories.support_repository import SupportTicketRepository
from app.services.admin.support import SupportAdministrationService
from app.utils.exceptions import ValidationException


def _resolve_test_database_url() -> str | None:
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    if value:
        return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == "TEST_DATABASE_URL":
            return raw_value.strip().strip('"')
    return None


@pytest_asyncio.fixture
async def support_db_session_factory():
    url = _resolve_test_database_url()
    if not url or not url.startswith("postgresql+asyncpg://"):
        pytest.skip("Support persistence tests require TEST_DATABASE_URL to point at the dedicated PostgreSQL test pooler.")

    parsed = urlsplit(url)
    if parsed.hostname is None or "pooler.supabase.com" not in parsed.hostname:
        pytest.skip("Support persistence tests require the dedicated Supabase session-pooler test target.")
    if "dqptbbwbrrtfkjjhqbug" not in (parsed.username or ""):
        pytest.skip("Support persistence tests require the project-specific test database identity.")

    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=2)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment guard for shared DB access
        pytest.skip(f"Support persistence tests require a reachable dedicated test database: {type(exc).__name__}: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _create_support_context(session_factory, *, suffix: str):
    async with session_factory() as session:
        admin_one = Admin(
            email=f"support-admin-{suffix}@example.test",
            username=f"support_admin_{suffix}",
            first_name="Support",
            last_name="Admin",
            role="admin",
            status="active",
            is_active=True,
        )
        admin_two = Admin(
            email=f"support-admin-two-{suffix}@example.test",
            username=f"support_admin_two_{suffix}",
            first_name="Second",
            last_name="Admin",
            role="admin",
            status="active",
            is_active=True,
        )
        customer = User(
            email=f"support-customer-{suffix}@example.test",
            username=f"support_customer_{suffix}",
            first_name="Support",
            last_name="Customer",
            status="active",
            is_active=True,
            email_verified=True,
            phone_verified=True,
        )
        session.add_all([admin_one, admin_two, customer])
        await session.flush()

        ticket = SupportTicket(
            user_id=customer.id,
            subject=f"Support ticket {suffix}",
            initial_message="Initial request",
            status="open",
            priority="normal",
            category="billing",
            assignee_id=admin_one.id,
        )
        session.add(ticket)
        await session.flush()
        await session.commit()
        return {
            "admin_one_id": admin_one.id,
            "admin_two_id": admin_two.id,
            "customer_id": customer.id,
            "ticket_id": ticket.id,
        }


async def _cleanup_support_context(session_factory, *, ticket_id, admin_ids, customer_id):
    async with session_factory() as session:
        await session.execute(delete(SupportMessage).where(SupportMessage.ticket_id == ticket_id))
        await session.execute(delete(SupportTicket).where(SupportTicket.id == ticket_id))
        if admin_ids:
            await session.execute(delete(Admin).where(Admin.id.in_(admin_ids)))
        await session.execute(delete(User).where(User.id == customer_id))
        await session.commit()


@pytest.mark.asyncio
async def test_support_ticket_persistence_round_trip_and_update_flow(support_db_session_factory):
    suffix = uuid4().hex[:8]
    context = await _create_support_context(support_db_session_factory, suffix=suffix)
    ticket_id = context["ticket_id"]
    admin_one_id = context["admin_one_id"]
    admin_two_id = context["admin_two_id"]
    customer_id = context["customer_id"]

    try:
        async with support_db_session_factory() as write_session:
            service = SupportAdministrationService(
                support_repository=SupportTicketRepository(write_session),
                admin_repository=AdminRepository(write_session),
            )

            stored_ticket = await service.get_support_ticket(ticket_id=str(ticket_id), admin_id=admin_one_id)
            assert stored_ticket["user_id"] == str(customer_id)
            assert stored_ticket["assignee_id"] == str(admin_one_id)

            customer_reply = await service.reply_to_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=admin_one_id,
                content="Customer-visible reply",
                is_internal=False,
            )
            assert any(message["content"] == "Customer-visible reply" and not message["is_internal"] for message in customer_reply["messages"])

            internal_note = await service.reply_to_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=admin_one_id,
                content="Internal note",
                is_internal=True,
            )
            assert any(message["content"] == "Internal note" and message["is_internal"] for message in internal_note["messages"])

            reassigned = await service.update_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=admin_one_id,
                assignee_id=admin_two_id,
                assignee_provided=True,
            )
            assert reassigned["assignee_id"] == str(admin_two_id)

            unassigned = await service.update_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=admin_one_id,
                assignee_id=None,
                assignee_provided=True,
            )
            assert unassigned["assignee_id"] is None

            retained_assignment = await service.update_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=admin_one_id,
                assignee_id=admin_two_id,
                assignee_provided=False,
            )
            assert retained_assignment["assignee_id"] == str(admin_two_id)

        async with support_db_session_factory() as read_session:
            read_ticket = await SupportTicketRepository(read_session).get_by_id(ticket_id)
            assert read_ticket is not None
            assert read_ticket.subject == f"Support ticket {suffix}"
            assert [message.content for message in read_ticket.messages if message.is_internal] == ["Internal note"]
            assert [message.content for message in read_ticket.messages if not message.is_internal] == ["Customer-visible reply"]
            assert read_ticket.assignee_id == admin_two_id
    finally:
        await _cleanup_support_context(
            support_db_session_factory,
            ticket_id=ticket_id,
            admin_ids=[admin_one_id, admin_two_id],
            customer_id=customer_id,
        )


@pytest.mark.asyncio
async def test_support_ticket_missing_ticket_and_invalid_assignee_are_rejected(support_db_session_factory):
    suffix = uuid4().hex[:8]
    context = await _create_support_context(support_db_session_factory, suffix=suffix)
    ticket_id = context["ticket_id"]
    admin_one_id = context["admin_one_id"]
    customer_id = context["customer_id"]

    try:
        async with support_db_session_factory() as session:
            service = SupportAdministrationService(
                support_repository=SupportTicketRepository(session),
                admin_repository=AdminRepository(session),
            )

            with pytest.raises(ValidationException, match="Support ticket was not found"):
                await service.get_support_ticket(ticket_id=str(uuid4()), admin_id=admin_one_id)

            with pytest.raises(ValidationException, match="does not exist"):
                await service.update_support_ticket(
                    ticket_id=str(ticket_id),
                    admin_id=admin_one_id,
                    assignee_id=uuid4(),
                    assignee_provided=True,
                )
    finally:
        await _cleanup_support_context(
            support_db_session_factory,
            ticket_id=ticket_id,
            admin_ids=[admin_one_id, context["admin_two_id"]],
            customer_id=customer_id,
        )


@pytest.mark.asyncio
async def test_support_service_commits_messages_and_assignment_to_isolated_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [User.__table__, Admin.__table__, SupportTicket.__table__, SupportMessage.__table__]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables))

        session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with session_factory() as seed_session:
            first_admin = Admin(
                email="first-support-admin@example.test",
                username="first_support_admin",
                role="admin",
                status="active",
                is_active=True,
            )
            second_admin = Admin(
                email="second-support-admin@example.test",
                username="second_support_admin",
                role="admin",
                status="active",
                is_active=True,
            )
            customer = User(email="support-customer@example.test", username="support_customer", status="active")
            seed_session.add_all([first_admin, second_admin, customer])
            await seed_session.flush()
            ticket = SupportTicket(
                user_id=customer.id,
                subject="Persistence verification",
                initial_message="I need help with a payment.",
                status="open",
                priority="normal",
                category="billing",
                assignee_id=first_admin.id,
            )
            seed_session.add(ticket)
            await seed_session.commit()
            first_admin_id = first_admin.id
            second_admin_id = second_admin.id
            ticket_id = ticket.id

        async with session_factory() as write_session:
            service = SupportAdministrationService(
                session=write_session,
                support_repository=SupportTicketRepository(write_session),
                admin_repository=AdminRepository(write_session),
            )
            await service.reply_to_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=first_admin_id,
                content="Customer-visible reply",
            )
            await service.reply_to_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=first_admin_id,
                content="Internal follow-up",
                is_internal=True,
            )
            await service.update_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=first_admin_id,
                assignee_id=second_admin_id,
                assignee_provided=True,
            )

        async with session_factory() as read_session:
            persisted_ticket = await SupportTicketRepository(read_session).get_by_id(ticket_id)
            assert persisted_ticket is not None
            assert persisted_ticket.initial_message == "I need help with a payment."
            assert persisted_ticket.assignee_id == second_admin_id
            assert [(message.content, message.is_internal) for message in persisted_ticket.messages] == [
                ("Customer-visible reply", False),
                ("Internal follow-up", True),
            ]

        async with session_factory() as unassign_session:
            service = SupportAdministrationService(
                session=unassign_session,
                support_repository=SupportTicketRepository(unassign_session),
                admin_repository=AdminRepository(unassign_session),
            )
            await service.update_support_ticket(
                ticket_id=str(ticket_id),
                admin_id=first_admin_id,
                assignee_id=None,
                assignee_provided=True,
            )

        async with session_factory() as verify_unassignment_session:
            persisted_ticket = await SupportTicketRepository(verify_unassignment_session).get_by_id(ticket_id)
            assert persisted_ticket is not None
            assert persisted_ticket.assignee_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_repository_lists_only_bounded_eligible_support_assignees():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=[Admin.__table__]))

        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            eligible_b = Admin(
                email="eligible-b@example.test",
                username="eligible_b",
                first_name="Bee",
                role="support",
                status="active",
                is_active=True,
            )
            eligible_a = Admin(
                email="eligible-a@example.test",
                username="eligible_a",
                first_name="Ada",
                role="admin",
                status="active",
                is_active=True,
            )
            inactive = Admin(
                email="inactive@example.test",
                username="inactive",
                role="admin",
                status="active",
                is_active=False,
            )
            disabled = Admin(
                email="disabled@example.test",
                username="disabled",
                role="support",
                status="disabled",
                is_active=True,
            )
            auditor = Admin(
                email="auditor@example.test",
                username="auditor",
                role="auditor",
                status="active",
                is_active=True,
            )
            session.add_all([eligible_b, eligible_a, inactive, disabled, auditor])
            await session.commit()

            repository = AdminRepository(session)
            assignees = await repository.get_support_assignees()
            assert [admin.id for admin in assignees] == [eligible_a.id, eligible_b.id]
            assert len(await repository.get_support_assignees(limit=1)) == 1
    finally:
        await engine.dispose()
