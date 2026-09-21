from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.repositories.audit_log_repository import AuditLogRepository
from app.utils.exceptions import ValidationException


class AuditService:
    """Service for audit-log review and export."""

    def __init__(self, logger: logging.Logger | None = None, audit_repository: AuditLogRepository | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.audit_repository = audit_repository

    async def list_audit_logs(self, **payload: Any) -> dict[str, Any]:
        if self.audit_repository is None:
            raise ValidationException(detail="Audit persistence is unavailable.", error_code="AUDIT_UNAVAILABLE")

        page = payload.get("page") or 1
        page_size = payload.get("page_size") or 20
        order_by = payload.get("order_by") or "created_at"
        descending = payload.get("descending")
        if descending is None:
            descending = True

        action = payload.get("action")
        category = payload.get("category")
        resource = payload.get("resource")
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        admin_id = payload.get("admin_id")

        if any(value is not None for value in (action, category, resource, start_date, end_date)):
            logs, total = await self.audit_repository.search_audit_logs(
                action=action,
                category=category,
                resource=resource,
                start_date=start_date if isinstance(start_date, datetime) else None,
                end_date=end_date if isinstance(end_date, datetime) else None,
                page=page,
                page_size=page_size,
                order_by=order_by,
                descending=descending,
            )
        else:
            logs, total = await self.audit_repository.get_admin_audit_logs(
                admin_id=admin_id,
                page=page,
                page_size=page_size,
                order_by=order_by,
                descending=descending,
            )

        items = [
            {
                "id": str(log.id),
                "actor_type": log.actor_type,
                "actor_id": log.actor_id,
                "action": log.action,
                "category": log.category,
                "description": log.description,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "request_id": log.request_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]
        return {
            "success": True,
            "data": {"items": items, "total": total},
            "meta": {"source": "admin.audit", "page": page, "page_size": page_size},
        }
