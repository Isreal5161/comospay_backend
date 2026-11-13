from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.config.settings import settings
from app.models.admin import Admin
from app.models.support import SupportMessage, SupportTicket
from app.repositories.support_repository import SupportTicketRepository
from app.utils.exceptions import ValidationException

_VALID_STATUSES = {"open", "in_progress", "waiting_customer", "resolved", "closed"}
_VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


class SupportAdministrationService:
    """Service for support ticket workflows backed by the SQLAlchemy repository layer."""

    def __init__(
        self,
        *,
        session: Any | None = None,
        support_repository: SupportTicketRepository | None = None,
        user_repository: Any | None = None,
        admin_repository: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.support_repository = support_repository or (SupportTicketRepository(session) if session is not None else None)
        self.session = session or getattr(self.support_repository, "session", None)
        self.user_repository = user_repository
        self.admin_repository = admin_repository

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    async def _commit_changes(self) -> None:
        if self.session is not None:
            await self.session.commit()

    @staticmethod
    def _normalize_status(status: str | None) -> str | None:
        if status is None:
            return None
        normalized = (status or "").strip().lower()
        if normalized not in _VALID_STATUSES:
            raise ValidationException("status must be one of: open, in_progress, waiting_customer, resolved, closed.")
        return normalized

    @staticmethod
    def _normalize_priority(priority: str | None) -> str | None:
        if priority is None:
            return None
        normalized = (priority or "").strip().lower()
        if normalized not in _VALID_PRIORITIES:
            raise ValidationException("priority must be one of: low, normal, high, urgent.")
        return normalized

    async def _validate_admin_identity(self, admin_id: UUID | None) -> UUID:
        if admin_id is None:
            raise ValidationException("An admin identity is required to update support tickets.")
        if self.admin_repository is None:
            return admin_id
        admin = await self.admin_repository.get_by_id(admin_id)
        if admin is None:
            raise ValidationException("The requested admin identity does not exist.")
        if (
            str(getattr(admin, "status", "") or "").strip().lower() != "active"
            or not bool(getattr(admin, "is_active", False))
        ):
            raise ValidationException("The requested admin identity is not active.")
        return admin_id

    @staticmethod
    def _eligible_support_roles() -> tuple[str, ...]:
        configured_roles = getattr(settings, "admin_allowed_roles", None)
        if isinstance(configured_roles, str):
            configured_roles = [role.strip() for role in configured_roles.split(",") if role.strip()]
        allowed_roles = configured_roles or ["super_admin", "admin"]
        return tuple(
            sorted(
                {
                    str(role).strip().lower().replace(" ", "_")
                    for role in allowed_roles
                    if str(role).strip()
                }
                & {"admin", "super_admin", "support"}
            )
        )

    async def _get_ticket(self, ticket_id: str) -> SupportTicket:
        if self.support_repository is None:
            raise ValidationException("Support ticket persistence is unavailable.")
        try:
            parsed_ticket_id = UUID(str(ticket_id))
        except (ValueError, TypeError) as exc:
            raise ValidationException("Support ticket identifier is invalid.") from exc

        ticket = await self.support_repository.get_by_id(parsed_ticket_id)
        if ticket is None:
            raise ValidationException("Support ticket was not found.")
        return ticket

    def _serialize_message(self, message: SupportMessage) -> dict[str, Any]:
        sender_id = None
        sender_name = "Support admin"
        sender_type = message.author_type
        if message.author_type == "customer":
            sender_id = message.customer_id
            sender_name = getattr(message.customer, "full_name", None) or self._customer_name(message.customer)
        elif message.admin_id is not None:
            sender_id = message.admin_id
            sender_name = self._admin_name(message.admin)
        return {
            "id": str(message.id),
            "sender_id": str(sender_id) if sender_id is not None else None,
            "sender_name": sender_name,
            "sender_type": sender_type,
            "content": message.content,
            "created_at": message.created_at.isoformat().replace("+00:00", "Z"),
            "is_internal": bool(message.is_internal),
        }

    @staticmethod
    def _visible_messages_for_context(messages: list[SupportMessage], *, include_internal: bool) -> list[SupportMessage]:
        if include_internal:
            return list(messages)
        return [message for message in messages if not bool(message.is_internal)]

    @staticmethod
    def _customer_name(user: Any) -> str:
        if user is None:
            return "Customer"
        parts = [
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        ]
        first_last = " ".join(part for part in parts if part)
        if first_last:
            return first_last
        return str(getattr(user, "username", None) or "Customer")

    @staticmethod
    def _admin_name(admin: Any) -> str:
        if admin is None:
            return "Support admin"
        parts = [
            getattr(admin, "first_name", None),
            getattr(admin, "last_name", None),
        ]
        first_last = " ".join(part for part in parts if part)
        if first_last:
            return first_last
        return str(getattr(admin, "username", None) or "Support admin")

    def _serialize_ticket(self, ticket: SupportTicket, *, include_internal: bool = True) -> dict[str, Any]:
        customer_name = self._customer_name(ticket.user)
        assignee_name = self._admin_name(ticket.assignee) if ticket.assignee is not None else None

        visible_messages = self._visible_messages_for_context(list(ticket.messages), include_internal=include_internal)
        return {
            "id": str(ticket.id),
            "user_id": str(ticket.user_id),
            "user_name": customer_name,
            "subject": ticket.subject,
            "initial_message": ticket.initial_message,
            "status": ticket.status,
            "priority": ticket.priority,
            "category": ticket.category,
            "assignee_id": str(ticket.assignee_id) if ticket.assignee_id is not None else None,
            "assignee_name": assignee_name,
            "created_at": ticket.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": ticket.updated_at.isoformat().replace("+00:00", "Z"),
            "messages": [self._serialize_message(message) for message in visible_messages],
        }

    async def list_support_tickets(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        admin_id: UUID | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValidationException("page must be >= 1 and page_size must be between 1 and 100.")
        if self.support_repository is None:
            raise ValidationException("Support ticket persistence is unavailable.")
        await self._validate_admin_identity(admin_id)

        normalized_status = self._normalize_status(status)
        normalized_priority = self._normalize_priority(priority)
        tickets, total = await self.support_repository.list_tickets(
            page=page,
            page_size=page_size,
            search=search,
            status=normalized_status,
            priority=normalized_priority,
        )
        return {
            "items": [self._serialize_ticket(ticket, include_internal=True) for ticket in tickets],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    async def list_support_assignees(self, *, admin_id: UUID | None = None) -> dict[str, Any]:
        await self._validate_admin_identity(admin_id)
        if self.admin_repository is None:
            raise ValidationException("Admin persistence is unavailable.")

        admins = await self.admin_repository.get_support_assignees(
            roles=self._eligible_support_roles(),
            limit=100,
        )
        return {
            "items": [
                {
                    "id": str(admin.id),
                    "display_name": " ".join(
                        part
                        for part in (
                            getattr(admin, "first_name", None),
                            getattr(admin, "last_name", None),
                        )
                        if part
                    )
                    or getattr(admin, "username", None)
                    or "Admin",
                }
                for admin in admins
            ]
        }

    async def get_support_ticket(self, *, ticket_id: str, admin_id: UUID | None = None, **payload: Any) -> dict[str, Any]:
        await self._validate_admin_identity(admin_id)
        ticket = await self._get_ticket(ticket_id)
        return self._serialize_ticket(ticket, include_internal=True)

    async def reply_to_support_ticket(
        self,
        *,
        ticket_id: str,
        admin_id: UUID | None,
        content: str,
        is_internal: bool = False,
        **payload: Any,
    ) -> dict[str, Any]:
        await self._validate_admin_identity(admin_id)
        ticket = await self._get_ticket(ticket_id)
        cleaned = (content or "").strip()
        if not cleaned:
            raise ValidationException("Support reply content is required.")

        message = SupportMessage(
            ticket_id=ticket.id,
            admin_id=admin_id,
            author_type="admin",
            content=cleaned,
            is_internal=bool(is_internal),
        )
        if ticket.status == "closed":
            ticket.status = "in_progress"
        ticket.updated_at = self._utc_now()
        await self.support_repository.create_message(message)
        ticket.messages.append(message)
        await self.support_repository.update_ticket(ticket)
        updated_ticket = await self.support_repository.get_by_id(ticket.id)
        serialized = self._serialize_ticket(updated_ticket)
        await self._commit_changes()
        return serialized

    async def update_support_ticket(
        self,
        *,
        ticket_id: str,
        admin_id: UUID | None,
        status: str | None = None,
        priority: str | None = None,
        assignee_id: UUID | None = None,
        assignee_provided: bool = False,
        note: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        await self._validate_admin_identity(admin_id)
        ticket = await self._get_ticket(ticket_id)

        if status is not None:
            ticket.status = self._normalize_status(status)
        if priority is not None:
            ticket.priority = self._normalize_priority(priority)
        if assignee_provided:
            if assignee_id is None:
                ticket.assignee_id = None
            else:
                if self.admin_repository is not None:
                    target_admin = await self.admin_repository.get_by_id(assignee_id)
                    if target_admin is None:
                        raise ValidationException("The target assignee does not exist.")
                    if (
                        str(getattr(target_admin, "status", "") or "").strip().lower() != "active"
                        or not bool(getattr(target_admin, "is_active", False))
                    ):
                        raise ValidationException("The target assignee is not active.")
                    target_role = str(getattr(target_admin, "role", "") or "").strip().lower().replace(" ", "_")
                    if target_role not in self._eligible_support_roles():
                        raise ValidationException("The target administrator is not eligible for Support assignment.")
                ticket.assignee_id = assignee_id

        if note is not None:
            cleaned_note = note.strip()
            if cleaned_note:
                message = SupportMessage(
                    ticket_id=ticket.id,
                    admin_id=admin_id,
                    author_type="admin",
                    content=cleaned_note,
                    is_internal=True,
                )
                ticket.messages.append(message)
                await self.support_repository.create_message(message)

        now = self._utc_now()
        ticket.updated_at = now
        if ticket.status == "resolved" and ticket.resolved_at is None:
            ticket.resolved_at = now
        if ticket.status == "closed" and ticket.closed_at is None:
            ticket.closed_at = now
        if ticket.status not in {"resolved", "closed"}:
            ticket.resolved_at = None if ticket.status != "resolved" else ticket.resolved_at
            ticket.closed_at = None if ticket.status != "closed" else ticket.closed_at

        await self.support_repository.update_ticket(ticket)
        updated_ticket = await self.support_repository.get_by_id(ticket.id)
        serialized = self._serialize_ticket(updated_ticket, include_internal=True)
        await self._commit_changes()
        return serialized

    async def _list_support_tickets(self, **payload: Any) -> dict[str, Any]:
        return await self.list_support_tickets(**payload)
