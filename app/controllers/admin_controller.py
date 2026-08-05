from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.schemas.admin_schema import AdminCreate, AdminUpdate
from app.schemas.api_key_schema import APIKeyCreate, APIKeyRevokeSchema, APIKeyUpdate
from app.schemas.audit_log_schema import AuditLogFilterSchema
from app.schemas.kyc_schema import KYCApprovalSchema
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class AdminService(Protocol):
    """Protocol for admin service dependency injection."""


class AdminDashboardRequest(BaseModel):
    """Request schema for dashboard queries."""

    period: str | None = Field(default=None, max_length=50, description="Optional reporting period.")
    start_date: str | None = Field(default=None, description="Optional start date filter.")
    end_date: str | None = Field(default=None, description="Optional end date filter.")


class UserManagementRequest(BaseModel):
    """Request schema for admin user management operations."""

    action: str = Field(..., max_length=50, description="User management action to perform.")
    reason: str | None = Field(default=None, description="Administrative reason for the action.")
    status: str | None = Field(default=None, max_length=50, description="Target user status.")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata payload.")


class WalletAdjustmentRequest(BaseModel):
    """Request schema for wallet adjustment operations."""

    user_id: UUID = Field(..., description="Identifier of the affected user.")
    amount: float = Field(..., description="Adjustment amount.")
    currency: str = Field(default="NGN", min_length=3, max_length=3, description="Currency code.")
    reason: str = Field(..., description="Reason for the adjustment.")
    reference: str | None = Field(default=None, description="Optional reference value.")


class TransactionReverseRequest(BaseModel):
    """Request schema for transaction reversal operations."""

    transaction_id: UUID = Field(..., description="Identifier of the transaction to reverse.")
    reason: str = Field(..., description="Reason for the reversal.")
    reference: str | None = Field(default=None, description="Optional reference value.")


class ProviderManagementRequest(BaseModel):
    """Request schema for provider management operations."""

    action: str = Field(..., max_length=50, description="Provider action to perform.")
    config: dict[str, Any] | None = Field(default=None, description="Optional provider configuration payload.")


class SystemSettingsRequest(BaseModel):
    """Request schema for updating system settings."""

    updates: dict[str, Any] = Field(..., description="System settings updates to apply.")


class NotificationBroadcastRequest(BaseModel):
    """Request schema for admin notification broadcasts."""

    message: str = Field(..., description="Broadcast message content.")
    title: str | None = Field(default=None, description="Optional broadcast title.")
    channel: str = Field(default="in_app", max_length=50, description="Notification channel.")
    recipients: list[UUID] | None = Field(default=None, description="Optional recipient user identifiers.")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional broadcast metadata.")


class ReportGenerationRequest(BaseModel):
    """Request schema for report generation requests."""

    report_type: str = Field(..., max_length=100, description="Report type to generate.")
    format: str = Field(default="json", max_length=20, description="Output format.")
    period: str | None = Field(default=None, max_length=50, description="Optional reporting period.")
    start_date: str | None = Field(default=None, description="Optional start date filter.")
    end_date: str | None = Field(default=None, description="Optional end date filter.")


class AdminController:
    """Thin FastAPI controller for administrative operations."""

    def __init__(self, admin_service: AdminService, logger: logging.Logger | None = None) -> None:
        self.admin_service = admin_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/admin", tags=["Admin"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.get("/dashboard", status_code=status.HTTP_200_OK)(self.get_dashboard)
        self.router.get("/users", status_code=status.HTTP_200_OK)(self.list_users)
        self.router.get("/users/{user_id}", status_code=status.HTTP_200_OK)(self.get_user)
        self.router.post("/users/{user_id}/manage", status_code=status.HTTP_200_OK)(self.manage_user)
        self.router.post("/kyc/{user_id}/review", status_code=status.HTTP_200_OK)(self.review_kyc)
        self.router.post("/wallets/adjust", status_code=status.HTTP_200_OK)(self.adjust_wallet)
        self.router.get("/transactions", status_code=status.HTTP_200_OK)(self.list_transactions)
        self.router.post("/transactions/{transaction_id}/reverse", status_code=status.HTTP_200_OK)(self.reverse_transaction)
        self.router.get("/providers", status_code=status.HTTP_200_OK)(self.list_providers)
        self.router.post("/providers/{provider_id}/configure", status_code=status.HTTP_200_OK)(self.configure_provider)
        self.router.get("/settings", status_code=status.HTTP_200_OK)(self.get_system_settings)
        self.router.put("/settings", status_code=status.HTTP_200_OK)(self.update_system_settings)
        self.router.get("/audit-logs", status_code=status.HTTP_200_OK)(self.list_audit_logs)
        self.router.post("/api-keys", status_code=status.HTTP_201_CREATED)(self.create_api_key)
        self.router.put("/api-keys/{api_key_id}", status_code=status.HTTP_200_OK)(self.update_api_key)
        self.router.post("/api-keys/{api_key_id}/revoke", status_code=status.HTTP_200_OK)(self.revoke_api_key)
        self.router.post("/notifications/broadcast", status_code=status.HTTP_201_CREATED)(self.broadcast_notification)
        self.router.get("/statistics", status_code=status.HTTP_200_OK)(self.get_platform_statistics)
        self.router.get("/monitoring", status_code=status.HTTP_200_OK)(self.get_service_monitoring)
        self.router.post("/reports", status_code=status.HTTP_200_OK)(self.generate_report)
        self.router.post("/admins", status_code=status.HTTP_201_CREATED)(self.create_admin_account)
        self.router.put("/admins/{admin_id}", status_code=status.HTTP_200_OK)(self.update_admin_account)

    async def get_dashboard(self, payload: AdminDashboardRequest | None = None, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle administrator dashboard requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        request_payload = payload.model_dump() if payload else {}
        return await self._execute(
            action="get_dashboard",
            handler=self._resolve_handler("get_dashboard_stats"),
            payload={"admin_id": target_admin_id, **request_payload},
            success_message="Dashboard data retrieved successfully.",
        )

    async def list_users(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle user management listing requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="list_users",
            handler=self._resolve_handler("list_users"),
            payload={"admin_id": target_admin_id},
            success_message="Users retrieved successfully.",
        )

    async def get_user(self, user_id: UUID, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle user lookup requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="get_user",
            handler=self._resolve_handler("get_user"),
            payload={"admin_id": target_admin_id, "user_id": user_id},
            success_message="User retrieved successfully.",
        )

    async def manage_user(self, user_id: UUID, payload: UserManagementRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle user management requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="manage_user",
            handler=self._resolve_handler("manage_user"),
            payload={
                "admin_id": target_admin_id,
                "user_id": user_id,
                "action": payload.action,
                "reason": payload.reason,
                "status": payload.status,
                "metadata": payload.metadata,
            },
            success_message="User management action completed successfully.",
        )

    async def review_kyc(self, user_id: UUID, payload: KYCApprovalSchema, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle KYC approval and rejection requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="review_kyc",
            handler=self._resolve_handler("review_kyc"),
            payload={
                "admin_id": target_admin_id,
                "user_id": user_id,
                "approval_status": payload.approval_status,
                "review_notes": payload.review_notes,
            },
            success_message="KYC review completed successfully.",
        )

    async def adjust_wallet(self, payload: WalletAdjustmentRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle wallet adjustment requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="adjust_wallet",
            handler=self._resolve_handler("adjust_wallet"),
            payload={
                "admin_id": target_admin_id,
                "user_id": payload.user_id,
                "amount": payload.amount,
                "currency": payload.currency,
                "reason": payload.reason,
                "reference": payload.reference,
            },
            success_message="Wallet adjustment completed successfully.",
        )

    async def list_transactions(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle transaction monitoring requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="list_transactions",
            handler=self._resolve_handler("list_transactions"),
            payload={"admin_id": target_admin_id},
            success_message="Transactions retrieved successfully.",
        )

    async def reverse_transaction(self, transaction_id: UUID, payload: TransactionReverseRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle transaction reversal requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="reverse_transaction",
            handler=self._resolve_handler("reverse_transaction"),
            payload={
                "admin_id": target_admin_id,
                "transaction_id": transaction_id,
                "reason": payload.reason,
                "reference": payload.reference,
            },
            success_message="Transaction reversal completed successfully.",
        )

    async def list_providers(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle provider management listing requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="list_providers",
            handler=self._resolve_handler("list_providers"),
            payload={"admin_id": target_admin_id},
            success_message="Providers retrieved successfully.",
        )

    async def configure_provider(self, provider_id: UUID, payload: ProviderManagementRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle provider configuration requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="configure_provider",
            handler=self._resolve_handler("configure_provider"),
            payload={
                "admin_id": target_admin_id,
                "provider_id": provider_id,
                "action": payload.action,
                "config": payload.config,
            },
            success_message="Provider configuration updated successfully.",
        )

    async def get_system_settings(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle system settings retrieval requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="get_system_settings",
            handler=self._resolve_handler("get_system_settings"),
            payload={"admin_id": target_admin_id},
            success_message="System settings retrieved successfully.",
        )

    async def update_system_settings(self, payload: SystemSettingsRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle system settings update requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="update_system_settings",
            handler=self._resolve_handler("update_system_settings"),
            payload={"admin_id": target_admin_id, "updates": payload.updates},
            success_message="System settings updated successfully.",
        )

    async def list_audit_logs(self, payload: AuditLogFilterSchema | None = None, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle audit log requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="list_audit_logs",
            handler=self._resolve_handler("list_audit_logs"),
            payload={"admin_id": target_admin_id, **request_payload},
            success_message="Audit logs retrieved successfully.",
        )

    async def create_api_key(self, payload: APIKeyCreate, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle API key creation requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="create_api_key",
            handler=self._resolve_handler("create_api_key"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="API key created successfully.",
        )

    async def update_api_key(self, api_key_id: UUID, payload: APIKeyUpdate, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle API key update requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="update_api_key",
            handler=self._resolve_handler("update_api_key"),
            payload={"admin_id": target_admin_id, "api_key_id": api_key_id, **payload.model_dump(exclude_unset=True)},
            success_message="API key updated successfully.",
        )

    async def revoke_api_key(self, api_key_id: UUID, payload: APIKeyRevokeSchema, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle API key revocation requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="revoke_api_key",
            handler=self._resolve_handler("revoke_api_key"),
            payload={"admin_id": target_admin_id, "api_key_id": api_key_id, **payload.model_dump(exclude_unset=True)},
            success_message="API key revoked successfully.",
        )

    async def broadcast_notification(self, payload: NotificationBroadcastRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle notification broadcast requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="broadcast_notification",
            handler=self._resolve_handler("broadcast_notification"),
            payload={
                "admin_id": target_admin_id,
                **payload.model_dump(exclude_unset=True),
            },
            success_message="Notification broadcast completed successfully.",
        )

    async def get_platform_statistics(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle platform statistics requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="get_platform_statistics",
            handler=self._resolve_handler("get_platform_statistics"),
            payload={"admin_id": target_admin_id},
            success_message="Platform statistics retrieved successfully.",
        )

    async def get_service_monitoring(self, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle service monitoring requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="get_service_monitoring",
            handler=self._resolve_handler("get_service_monitoring"),
            payload={"admin_id": target_admin_id},
            success_message="Service monitoring data retrieved successfully.",
        )

    async def generate_report(self, payload: ReportGenerationRequest, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle report generation requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="generate_report",
            handler=self._resolve_handler("generate_report"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="Report generated successfully.",
        )

    async def create_admin_account(self, payload: AdminCreate, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle administrator account creation requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="create_admin_account",
            handler=self._resolve_handler("create_admin_account"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="Admin account created successfully.",
        )

    async def update_admin_account(self, admin_id_value: UUID, payload: AdminUpdate, admin_id: UUID | None = None) -> dict[str, Any]:
        """Handle administrator account update requests."""
        target_admin_id = admin_id or self._resolve_admin_id()
        return await self._execute(
            action="update_admin_account",
            handler=self._resolve_handler("update_admin_account"),
            payload={"admin_id": target_admin_id, "target_admin_id": admin_id_value, **payload.model_dump(exclude_unset=True)},
            success_message="Admin account updated successfully.",
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

        log_api_event(self.logger, "admin_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "admin_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the request.",
        )

    def _resolve_handler(self, method_name: str) -> Callable[..., Awaitable[Any]]:
        handler = getattr(self.admin_service, method_name, None)
        if handler is None or not callable(handler):
            raise AttributeError(f"Admin service does not implement '{method_name}'.")
        return cast(Callable[..., Awaitable[Any]], handler)

    def _resolve_admin_id(self) -> UUID:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
