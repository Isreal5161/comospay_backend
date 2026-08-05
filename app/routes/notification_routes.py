from __future__ import annotations

"""Notification route registration for the CosmozPay backend."""

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.config.redis import get_redis
from app.controllers.notification_controller import NotificationController, NotificationPreferencesUpdateRequest
from app.repositories.notification_repository import NotificationRepository
from app.repositories.provider_repository import ProviderRepository
from app.schemas.notification_schema import NotificationCreate, NotificationReadSchema
from app.services.notification.email import EmailNotificationService
from app.services.notification.in_app import InAppNotificationService
from app.services.notification.preferences import NotificationPreferencesService
from app.repositories.notification_preferences_repository import NotificationPreferencesRepository
from app.services.notification.push import PushNotificationService
from app.services.notification.sms import SMSNotificationService
from app.services.notification_service import NotificationService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService


router = APIRouter(prefix="/notifications", tags=["Notifications"])


def build_notification_service(*, session: AsyncSession, redis_client: Any | None = None) -> NotificationService:
    provider_repository = ProviderRepository(session=session)
    provider_selector = ProviderSelector(provider_repository=provider_repository)
    provider_health_service = ProviderHealthService(provider_repository=provider_repository)
    provider_failover_service = ProviderFailoverService(
        selector=provider_selector,
        health_service=provider_health_service,
    )
    provider_service = ProviderService(
        selector=provider_selector,
        health_service=provider_health_service,
        failover_service=provider_failover_service,
        provider_repository=provider_repository,
    )

    notification_repository = NotificationRepository(session=session)
    preferences_repository = NotificationPreferencesRepository(session=session)
    return NotificationService(
        email_service=EmailNotificationService(provider_service=provider_service),
        sms_service=SMSNotificationService(provider_service=provider_service),
        push_service=PushNotificationService(provider_service=provider_service),
        in_app_service=InAppNotificationService(repository=notification_repository),
        preferences_service=NotificationPreferencesService(
            preferences_repository=preferences_repository,
            redis_client=redis_client,
        ),
    )


async def get_notification_service(session: AsyncSession = Depends(get_db)) -> NotificationService:
    """Compose the notification service graph per request using the active database session."""
    redis_client = await get_redis()
    return build_notification_service(session=session, redis_client=redis_client)


async def get_notification_controller(
    service: NotificationService = Depends(get_notification_service),
) -> NotificationController:
    """Instantiate the notification controller with a request-scoped notification service."""
    return NotificationController(service)


@router.get("", status_code=status.HTTP_200_OK)
async def list_notifications(
    page: int = 1,
    page_size: int = 20,
    order_by: str = "created_at",
    descending: bool = True,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.list_notifications(
        page=page,
        page_size=page_size,
        order_by=order_by,
        descending=descending,
    )


@router.get("/unread", status_code=status.HTTP_200_OK)
async def list_unread_notifications(
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.list_unread_notifications()


@router.get("/preferences", status_code=status.HTTP_200_OK)
async def get_preferences(
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.get_preferences()


@router.put("/preferences", status_code=status.HTTP_200_OK)
async def update_preferences(
    payload: NotificationPreferencesUpdateRequest,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.update_preferences(payload)


@router.get("/count", status_code=status.HTTP_200_OK)
async def get_notification_count(
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.get_notification_count()


@router.post("/mark-read", status_code=status.HTTP_200_OK)
async def mark_as_read(
    payload: NotificationReadSchema,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.mark_as_read(payload)


@router.post("/mark-all-read", status_code=status.HTTP_200_OK)
async def mark_all_as_read(
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.mark_all_as_read()


@router.post("/test", status_code=status.HTTP_201_CREATED)
async def send_test_notification(
    payload: NotificationCreate,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.send_test_notification(payload)


@router.get("/{notification_id}", status_code=status.HTTP_200_OK)
async def get_notification_detail(
    notification_id: str,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.get_notification_detail(notification_id=UUID(notification_id))


@router.delete("/{notification_id}", status_code=status.HTTP_200_OK)
async def delete_notification(
    notification_id: str,
    controller: NotificationController = Depends(get_notification_controller),
) -> dict[str, Any]:
    return await controller.delete_notification(notification_id=UUID(notification_id))
