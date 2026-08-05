from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.schemas.notification_schema import NotificationCreate, NotificationReadSchema
from app.services.notification_service import NotificationService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class NotificationListRequest(BaseModel):
    """Request schema for paginated notification listing."""

    page: int = Field(default=1, ge=1, description="Page number to retrieve.")
    page_size: int = Field(default=20, ge=1, le=100, description="Number of notifications per page.")
    order_by: str = Field(default="created_at", description="Field used to order notifications.")
    descending: bool = Field(default=True, description="Whether to return notifications in descending order.")


class NotificationPreferencesUpdateRequest(BaseModel):
    """Request schema for notification preference updates."""

    updates: dict[str, Any] = Field(..., description="Notification preference updates to apply.")


class NotificationController:
    """Thin FastAPI controller for notification operations."""

    def __init__(self, notification_service: NotificationService, logger: logging.Logger | None = None) -> None:
        self.notification_service = notification_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/notifications", tags=["Notifications"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.get("", status_code=status.HTTP_200_OK)(self.list_notifications)
        self.router.get("/unread", status_code=status.HTTP_200_OK)(self.list_unread_notifications)
        self.router.get("/preferences", status_code=status.HTTP_200_OK)(self.get_preferences)
        self.router.put("/preferences", status_code=status.HTTP_200_OK)(self.update_preferences)
        self.router.get("/count", status_code=status.HTTP_200_OK)(self.get_notification_count)
        self.router.post("/mark-read", status_code=status.HTTP_200_OK)(self.mark_as_read)
        self.router.post("/mark-all-read", status_code=status.HTTP_200_OK)(self.mark_all_as_read)
        self.router.post("/test", status_code=status.HTTP_201_CREATED)(self.send_test_notification)
        self.router.get("/{notification_id}", status_code=status.HTTP_200_OK)(self.get_notification_detail)
        self.router.delete("/{notification_id}", status_code=status.HTTP_200_OK)(self.delete_notification)

    async def list_notifications(
        self,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Handle notification listing requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="list_notifications",
            handler=self.notification_service.get_user_notifications,
            payload={
                "user_id": target_user_id,
                "page": page,
                "page_size": page_size,
                "order_by": order_by,
                "descending": descending,
            },
            success_message="Notifications retrieved successfully.",
        )

    async def list_unread_notifications(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle unread notification requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="list_unread_notifications",
            handler=self.notification_service.get_unread_notifications,
            payload={"user_id": target_user_id},
            success_message="Unread notifications retrieved successfully.",
        )

    async def get_notification_detail(self, notification_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle notification detail requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="get_notification_detail",
            handler=self.notification_service.get_user_notifications,
            payload={
                "user_id": target_user_id,
                "page": 1,
                "page_size": 1000,
                "order_by": "created_at",
                "descending": True,
            },
            success_message="Notification details retrieved successfully.",
        )

    async def get_preferences(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle notification preference retrieval requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="get_preferences",
            handler=self.notification_service.get_preferences,
            payload={"user_id": target_user_id},
            success_message="Notification preferences retrieved successfully.",
        )

    async def update_preferences(
        self,
        payload: NotificationPreferencesUpdateRequest,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Handle notification preference update requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="update_preferences",
            handler=self.notification_service.update_preferences,
            payload={"user_id": target_user_id, "updates": payload.updates},
            success_message="Notification preferences updated successfully.",
        )

    async def mark_as_read(self, payload: NotificationReadSchema, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle mark-as-read requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="mark_as_read",
            handler=self.notification_service.mark_as_read,
            payload={"notification_id": payload.notification_id},
            success_message="Notification marked as read successfully.",
        )

    async def mark_all_as_read(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle mark-all-as-read requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="mark_all_as_read",
            handler=self.notification_service.mark_all_as_read,
            payload={"user_id": target_user_id},
            success_message="All notifications marked as read successfully.",
        )

    async def delete_notification(self, notification_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle notification deletion requests."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="delete_notification",
            handler=self.notification_service.delete_notification,
            payload={"notification_id": notification_id},
            success_message="Notification deleted successfully.",
        )

    async def get_notification_count(self, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle notification count requests."""
        target_user_id = user_id or self._resolve_user_id()
        result = await self.notification_service.get_user_notifications(
            user_id=target_user_id,
            page=1,
            page_size=1000,
            order_by="created_at",
            descending=True,
        )
        return success_response(
            data={"count": result.get("total", 0)},
            message="Notification count retrieved successfully.",
        )

    async def send_test_notification(self, payload: NotificationCreate, user_id: UUID | None = None) -> dict[str, Any]:
        """Handle test notification requests for internal use."""
        target_user_id = user_id or self._resolve_user_id()
        return await self._execute(
            action="send_test_notification",
            handler=self.notification_service.create_notification,
            payload={
                "user_id": target_user_id or payload.user_id,
                "title": payload.title,
                "message": payload.message,
                "notification_type": payload.notification_type,
                "category": payload.category,
                "reference": payload.reference,
                "metadata": None,
                "channel": payload.channel,
                "priority": None,
                "expires_at": None,
            },
            success_message="Test notification sent successfully.",
        )

    async def _execute(
        self,
        action: str,
        handler: Callable[..., Awaitable[Any]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            result = await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

        log_api_event(self.logger, "notification_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "notification_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )

    def _resolve_user_id(self) -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
