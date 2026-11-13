from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request

from app.config.settings import settings
from app.controllers.admin_controller import AdminController
from app.models.support import SupportMessage, SupportTicket
from app.services.admin.support import SupportAdministrationService
from app.utils.exceptions import AuthorizationException, ValidationException


@pytest.fixture(autouse=True)
def support_allowed_roles(monkeypatch):
    monkeypatch.setattr(settings, "admin_allowed_roles", ["admin", "super_admin", "support"])


@pytest.fixture
def support_admin_id() -> UUID:
    return UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def support_customer_id() -> UUID:
    return UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def support_second_admin_id() -> UUID:
    return UUID("33333333-3333-4333-8333-333333333333")


class FakeSupportRepository:
    def __init__(self, ticket: SupportTicket) -> None:
        self.ticket = ticket
        self.messages: list[SupportMessage] = []

    async def get_by_id(self, ticket_id: UUID) -> SupportTicket | None:
        return self.ticket if self.ticket.id == ticket_id else None

    async def list_tickets(self, **kwargs):
        return [self.ticket], 1

    async def create_message(self, message: SupportMessage) -> SupportMessage:
        self.messages.append(message)
        return message

    async def update_ticket(self, ticket: SupportTicket) -> SupportTicket:
        self.ticket = ticket
        return ticket


class FakeAdminRepository:
    def __init__(
        self,
        active_admin_ids: set[UUID] | None = None,
        inactive_admin_ids: set[UUID] | None = None,
        support_assignees: list[SimpleNamespace] | None = None,
        ineligible_admin_ids: set[UUID] | None = None,
    ) -> None:
        self.active_admin_ids = active_admin_ids or set()
        self.inactive_admin_ids = inactive_admin_ids or set()
        self.support_assignees = support_assignees or []
        self.ineligible_admin_ids = ineligible_admin_ids or set()

    async def get_by_id(self, admin_id: UUID):
        if admin_id in self.active_admin_ids:
            return SimpleNamespace(id=admin_id, status="active", is_active=True, role="admin")
        if admin_id in self.inactive_admin_ids:
            return SimpleNamespace(id=admin_id, status="inactive", is_active=False, role="admin")
        if admin_id in self.ineligible_admin_ids:
            return SimpleNamespace(id=admin_id, status="active", is_active=True, role="auditor")
        return None

    async def get_support_assignees(self, *, roles: tuple[str, ...], limit: int):
        return [
            admin
            for admin in self.support_assignees
            if str(getattr(admin, "role", "")).strip().lower().replace(" ", "_") in roles
        ][:limit]


def make_request(identity_type: str | None, *, user_id: UUID, role: str = "support") -> Request:
    scope = {"type": "http", "method": "GET", "path": "/admin/support", "headers": []}
    request = Request(scope)
    request.state.auth_user = SimpleNamespace(user_id=user_id, identity_type=identity_type, role=role)
    request.state.user = request.state.auth_user
    return request


@pytest.mark.asyncio
async def test_support_controller_requires_authenticated_admin_identity(support_admin_id, support_customer_id):
    controller = AdminController(admin_service=SimpleNamespace())

    admin_request = make_request("admin", user_id=support_admin_id)
    assert controller._require_authenticated_admin_id(admin_request) == support_admin_id

    customer_request = make_request("user", user_id=support_customer_id)
    with pytest.raises(AuthorizationException):
        controller._require_authenticated_admin_id(customer_request)

    unauthenticated_request = Request({"type": "http", "method": "GET", "path": "/admin/support", "headers": []})
    with pytest.raises(AuthorizationException):
        controller._require_authenticated_admin_id(unauthenticated_request)


@pytest.mark.asyncio
async def test_support_assignee_controller_uses_authenticated_admin_identity(support_admin_id, support_customer_id):
    class AssigneeListingService:
        received_admin_id = None

        async def list_support_assignees(self, *, admin_id):
            self.received_admin_id = admin_id
            return {"items": [{"id": str(support_second_admin_id), "display_name": "Support Agent"}]}

    service = AssigneeListingService()
    controller = AdminController(admin_service=service)
    response = await controller.list_support_assignees(request=make_request("admin", user_id=support_admin_id))

    assert service.received_admin_id == support_admin_id
    assert response["data"]["items"] == [{"id": str(support_second_admin_id), "display_name": "Support Agent"}]
    with pytest.raises(AuthorizationException):
        await controller.list_support_assignees(request=make_request("user", user_id=support_customer_id))


@pytest.mark.asyncio
async def test_support_assignee_service_returns_only_safe_display_fields(support_admin_id, support_second_admin_id):
    admin_record = SimpleNamespace(
        id=support_second_admin_id,
        first_name="Support",
        last_name="Agent",
        username="support-agent",
        email="private@example.test",
        role="admin",
        is_active=True,
        status="active",
    )
    service = SupportAdministrationService(
        support_repository=SimpleNamespace(),
        admin_repository=FakeAdminRepository(
            active_admin_ids={support_admin_id},
            support_assignees=[admin_record],
        ),
    )

    result = await service.list_support_assignees(admin_id=support_admin_id)
    assert result == {"items": [{"id": str(support_second_admin_id), "display_name": "Support Agent"}]}

    with pytest.raises(ValidationException, match="admin identity"):
        await service.list_support_assignees(admin_id=None)


def test_support_ticket_serialization_uses_display_names_not_email_addresses():
    customer = SimpleNamespace(first_name=None, last_name=None, username="customer-handle", email="private-customer@example.test")
    admin = SimpleNamespace(
        id=UUID("efefefef-efef-4fef-8fef-efefefefefef"),
        first_name=None,
        last_name=None,
        username="support-handle",
        email="private-admin@example.test",
    )
    now = datetime.now(timezone.utc)
    ticket = SimpleNamespace(
        id=UUID("abababab-abab-4bab-8bab-abababababab"),
        user_id=UUID("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd"),
        user=customer,
        subject="Privacy check",
        initial_message="Please help",
        status="open",
        priority="normal",
        category="general",
        assignee_id=admin.id,
        assignee=admin,
        created_at=now,
        updated_at=now,
        messages=[
            SimpleNamespace(
                id=UUID("12121212-1212-4212-8212-121212121212"),
                author_type="admin",
                admin_id=admin.id,
                customer_id=None,
                admin=admin,
                customer=None,
                content="We are reviewing this.",
                created_at=now,
                is_internal=False,
            )
        ],
    )
    serialized = SupportAdministrationService(
        support_repository=SimpleNamespace(),
    )._serialize_ticket(ticket)

    assert serialized["user_name"] == "customer-handle"
    assert serialized["assignee_name"] == "support-handle"
    assert serialized["messages"][0]["sender_name"] == "support-handle"
    assert "private-customer@example.test" not in repr(serialized)
    assert "private-admin@example.test" not in repr(serialized)


@pytest.mark.asyncio
async def test_support_ticket_service_rejects_customer_or_missing_admin_identity(support_admin_id, support_customer_id):
    user_id = UUID("44444444-4444-4444-8444-444444444444")
    ticket = SupportTicket(
        user_id=user_id,
        subject="Billing issue",
        initial_message="Need help",
        status="open",
        priority="normal",
        category="billing",
    )
    ticket.id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    now = datetime.now(timezone.utc)
    ticket.created_at = now
    ticket.updated_at = now
    repo = FakeSupportRepository(ticket)
    service = SupportAdministrationService(
        support_repository=repo,
        admin_repository=FakeAdminRepository({support_admin_id}),
    )

    result = await service.list_support_tickets(page=1, page_size=10, admin_id=support_admin_id)
    assert result["items"][0]["user_id"] == str(user_id)

    with pytest.raises(ValidationException, match="admin identity"):
        await service.list_support_tickets(page=1, page_size=10, admin_id=support_customer_id)

    with pytest.raises(ValidationException, match="admin identity"):
        await service.get_support_ticket(ticket_id=str(ticket.id), admin_id=None)


@pytest.mark.asyncio
async def test_support_ticket_service_keeps_internal_notes_private_to_admins(support_admin_id, support_customer_id):
    user_id = UUID("55555555-5555-4555-8555-555555555555")
    ticket = SupportTicket(
        user_id=user_id,
        subject="Account issue",
        initial_message="Account access issue",
        status="open",
        priority="high",
        category="account",
    )
    ticket.id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    now = datetime.now(timezone.utc)
    ticket.created_at = now
    ticket.updated_at = now
    customer_message = SupportMessage(
        ticket_id=ticket.id,
        customer_id=user_id,
        author_type="customer",
        content="Customer message",
    )
    customer_message.created_at = now
    internal_message = SupportMessage(
        ticket_id=ticket.id,
        admin_id=support_admin_id,
        author_type="admin",
        content="Internal note",
        is_internal=True,
    )
    internal_message.created_at = now
    ticket.messages = [customer_message, internal_message]
    service = SupportAdministrationService(
        support_repository=FakeSupportRepository(ticket),
        admin_repository=FakeAdminRepository({support_admin_id}),
    )

    serialized = service._serialize_ticket(ticket, include_internal=False)
    assert all(not item["is_internal"] for item in serialized["messages"])

    admin_serialized = service._serialize_ticket(ticket, include_internal=True)
    assert any(item["is_internal"] for item in admin_serialized["messages"])

    with pytest.raises(ValidationException):
        await service.reply_to_support_ticket(
            ticket_id=str(ticket.id),
            admin_id=support_customer_id,
            content="Not allowed",
        )


@pytest.mark.asyncio
async def test_support_ticket_assignment_unassignment_respects_explicit_null_and_valid_admins(support_admin_id, support_second_admin_id, support_customer_id):
    user_id = UUID("66666666-6666-4666-8666-666666666666")
    ticket = SupportTicket(
        user_id=user_id,
        subject="Payment issue",
        initial_message="Payment failed",
        status="open",
        priority="normal",
        category="payments",
        assignee_id=support_admin_id,
    )
    ticket.id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    now = datetime.now(timezone.utc)
    ticket.created_at = now
    ticket.updated_at = now
    repo = FakeSupportRepository(ticket)
    service = SupportAdministrationService(
        support_repository=repo,
        admin_repository=FakeAdminRepository({support_admin_id, support_second_admin_id}),
    )

    await service.update_support_ticket(
        ticket_id=str(ticket.id),
        admin_id=support_admin_id,
        assignee_id=support_second_admin_id,
        assignee_provided=True,
    )
    assert repo.ticket.assignee_id == support_second_admin_id

    await service.update_support_ticket(
        ticket_id=str(ticket.id),
        admin_id=support_admin_id,
        assignee_id=None,
        assignee_provided=True,
    )
    assert repo.ticket.assignee_id is None

    with pytest.raises(ValidationException, match="does not exist"):
        await service.update_support_ticket(
            ticket_id=str(ticket.id),
            admin_id=support_admin_id,
            assignee_id=support_customer_id,
            assignee_provided=True,
        )

    handle = await service.update_support_ticket(
        ticket_id=str(ticket.id),
        admin_id=support_admin_id,
        assignee_id=support_second_admin_id,
        assignee_provided=True,
    )
    assert handle["assignee_id"] == str(support_second_admin_id)


@pytest.mark.asyncio
async def test_support_ticket_rejects_inactive_admin_as_assignee(support_admin_id):
    user_id = UUID("77777777-7777-4777-8777-777777777777")
    inactive_admin_id = UUID("88888888-8888-4888-8888-888888888888")
    ticket = SupportTicket(
        user_id=user_id,
        subject="Inactive assignment",
        initial_message="Need assistance",
        status="open",
        priority="normal",
        category="general",
    )
    ticket.id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    now = datetime.now(timezone.utc)
    ticket.created_at = now
    ticket.updated_at = now
    service = SupportAdministrationService(
        support_repository=FakeSupportRepository(ticket),
        admin_repository=FakeAdminRepository(
            active_admin_ids={support_admin_id},
            inactive_admin_ids={inactive_admin_id},
        ),
    )

    with pytest.raises(ValidationException, match="assignee is not active"):
        await service.update_support_ticket(
            ticket_id=str(ticket.id),
            admin_id=support_admin_id,
            assignee_id=inactive_admin_id,
            assignee_provided=True,
        )


@pytest.mark.asyncio
async def test_support_ticket_rejects_active_but_ineligible_admin_as_assignee(support_admin_id):
    user_id = UUID("99999999-9999-4999-8999-999999999999")
    auditor_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    ticket = SupportTicket(
        user_id=user_id,
        subject="Auditor assignment",
        initial_message="Need assistance",
        status="open",
        priority="normal",
        category="general",
    )
    ticket.id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    now = datetime.now(timezone.utc)
    ticket.created_at = now
    ticket.updated_at = now
    service = SupportAdministrationService(
        support_repository=FakeSupportRepository(ticket),
        admin_repository=FakeAdminRepository(
            active_admin_ids={support_admin_id},
            ineligible_admin_ids={auditor_id},
        ),
    )

    with pytest.raises(ValidationException, match="not eligible for Support assignment"):
        await service.update_support_ticket(
            ticket_id=str(ticket.id),
            admin_id=support_admin_id,
            assignee_id=auditor_id,
            assignee_provided=True,
        )
