from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.schemas.admin_schema import AdminCreate, AdminUpdate
from app.schemas.api_key_schema import APIKeyCreate, APIKeyRevokeSchema, APIKeyUpdate
from app.schemas.audit_log_schema import AuditLogFilterSchema
from app.schemas.kyc_schema import KYCApprovalSchema
from app.utils.exceptions import AppException, AuthenticationException, AuthorizationException
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


class WalletStateChangeRequest(BaseModel):
    """Request schema for wallet state change operations."""

    reason: str | None = Field(default=None, description="Optional reason for the state change.")


class WalletFundsMutationRequest(BaseModel):
    """Request schema for wallet lock and unlock operations."""

    amount: float = Field(..., description="Amount to mutate.")
    reason: str = Field(..., description="Reason for the operation.")
    reference: str | None = Field(default=None, description="Optional reference value.")


class WalletReconciliationRequest(BaseModel):
    """Request schema for wallet reconciliation operations."""

    dry_run: bool = Field(default=True, description="Whether to run reconciliation in dry-run mode.")


class TransactionReverseRequest(BaseModel):
    """Request schema for transaction reversal operations."""

    transaction_id: UUID = Field(..., description="Identifier of the transaction to reverse.")
    reason: str = Field(..., description="Reason for the reversal.")
    reference: str | None = Field(default=None, description="Optional reference value.")


class TransactionRetryRequest(BaseModel):
    """Request schema for transaction retry operations."""

    reason: str | None = Field(default=None, description="Optional reason for retrying the transaction.")


class TransactionResolveRequest(BaseModel):
    """Request schema for transaction resolution operations."""

    provider_status: str | None = Field(default=None, description="Optional provider status used for reconciliation.")


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
        self.router.get("/me", status_code=status.HTTP_200_OK)(self.get_me)
        self.router.get("/users", status_code=status.HTTP_200_OK)(self.list_users)
        self.router.get("/users/{user_id}", status_code=status.HTTP_200_OK)(self.get_user)
        self.router.post("/users/{user_id}/manage", status_code=status.HTTP_200_OK)(self.manage_user)
        self.router.post("/kyc/{user_id}/review", status_code=status.HTTP_200_OK)(self.review_kyc)
        self.router.post("/wallets/adjust", status_code=status.HTTP_200_OK)(self.adjust_wallet)
        self.router.get("/wallets", status_code=status.HTTP_200_OK)(self.list_wallets)
        self.router.get("/wallets/search", status_code=status.HTTP_200_OK)(self.search_wallets)
        self.router.get("/wallets/{wallet_id}", status_code=status.HTTP_200_OK)(self.get_wallet_details)
        self.router.get("/wallets/user/{user_id}", status_code=status.HTTP_200_OK)(self.get_user_wallet)
        self.router.get("/wallets/{wallet_id}/history", status_code=status.HTTP_200_OK)(self.wallet_history)
        self.router.get("/wallets/{wallet_id}/analytics", status_code=status.HTTP_200_OK)(self.wallet_analytics)
        self.router.post("/wallets/{wallet_id}/freeze", status_code=status.HTTP_200_OK)(self.freeze_wallet)
        self.router.post("/wallets/{wallet_id}/unfreeze", status_code=status.HTTP_200_OK)(self.unfreeze_wallet)
        self.router.post("/wallets/{wallet_id}/lock", status_code=status.HTTP_200_OK)(self.lock_wallet)
        self.router.post("/wallets/{wallet_id}/unlock", status_code=status.HTTP_200_OK)(self.unlock_wallet)
        self.router.post("/wallets/{wallet_id}/reconcile", status_code=status.HTTP_200_OK)(self.reconcile_wallet)
        self.router.get("/transactions", status_code=status.HTTP_200_OK)(self.list_transactions)
        self.router.get("/transactions/{transaction_id}", status_code=status.HTTP_200_OK)(self.get_transaction_details)
        self.router.post("/transactions/{transaction_id}/reverse", status_code=status.HTTP_200_OK)(self.reverse_transaction)
        self.router.post("/transactions/{transaction_id}/retry", status_code=status.HTTP_200_OK)(self.retry_failed_transaction)
        self.router.post("/transactions/{transaction_id}/resolve", status_code=status.HTTP_200_OK)(self.resolve_transaction)
        self.router.get("/transactions/{transaction_id}/timeline", status_code=status.HTTP_200_OK)(self.transaction_timeline)
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
        self.router.post("/sessions/{session_id}/revoke", status_code=status.HTTP_200_OK)(self.revoke_user_session)
        self.router.get("/users/{user_id}/sessions", status_code=status.HTTP_200_OK)(self.list_user_sessions)
        self.router.post("/users/{user_id}/sessions/revoke-all", status_code=status.HTTP_200_OK)(self.revoke_all_user_sessions)
        self.router.get("/users/{user_id}/sessions/count", status_code=status.HTTP_200_OK)(self.get_active_session_count)

    async def get_dashboard(self, payload: AdminDashboardRequest | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle administrator dashboard requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump() if payload else {}
        return await self._execute(
            action="get_dashboard",
            handler=self._resolve_handler("get_dashboard_stats"),
            payload={"admin_id": target_admin_id, **request_payload},
            success_message="Dashboard data retrieved successfully.",
        )

    async def get_me(self, request: Request | None = None) -> dict[str, Any]:
        """Resolve and return the authoritative profile for the Admin JWT identity."""
        auth_user = getattr(request.state, "auth_user", None) if request is not None else None
        if auth_user is None or getattr(auth_user, "identity_type", "user") != "admin":
            raise AuthorizationException(detail="An Admin identity is required.")
        raw_admin_id = getattr(auth_user, "user_id", None)
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthenticationException("Admin identity is invalid.") from exc
        return await self._execute(
            action="get_admin_profile",
            handler=self._resolve_handler("get_admin_profile"),
            payload={"admin_id": admin_id, "token_role": str(getattr(auth_user, "role", ""))},
            success_message="Admin profile retrieved successfully.",
        )

    async def list_users(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle user management listing requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="list_users",
            handler=self._resolve_handler("list_users"),
            payload={"admin_id": target_admin_id},
            success_message="Users retrieved successfully.",
        )

    async def get_user(self, user_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle user lookup requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_user",
            handler=self._resolve_handler("get_user"),
            payload={"admin_id": target_admin_id, "user_id": user_id},
            success_message="User retrieved successfully.",
        )

    async def manage_user(self, user_id: UUID, payload: UserManagementRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle user management requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
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

    async def review_kyc(self, user_id: UUID, payload: KYCApprovalSchema, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle KYC approval and rejection requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="review_kyc",
            handler=self._resolve_handler("review_kyc"),
            payload={
                "admin_id": target_admin_id,
                "user_id": user_id,
                "action": payload.approval_status,
                "review_notes": payload.review_notes,
            },
            success_message="KYC review completed successfully.",
        )

    async def adjust_wallet(self, payload: WalletAdjustmentRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet adjustment requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
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

    async def list_wallets(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        wallet_type: str | None = None,
        currency: str | None = None,
        query: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet listing requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="list_wallets",
            handler=self._resolve_handler("list_wallets"),
            payload={
                "admin_id": target_admin_id,
                "page": page,
                "page_size": page_size,
                "status": status,
                "wallet_type": wallet_type,
                "currency": currency,
                "query": query,
                "order_by": order_by,
                "descending": descending,
            },
            success_message="Wallets retrieved successfully.",
        )

    async def get_wallet_details(self, wallet_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet detail lookup requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_wallet_details",
            handler=self._resolve_handler("get_wallet_details"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id},
            success_message="Wallet retrieved successfully.",
        )

    async def get_user_wallet(self, user_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle user wallet lookup requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_user_wallet",
            handler=self._resolve_handler("get_user_wallet"),
            payload={"admin_id": target_admin_id, "user_id": user_id},
            success_message="User wallet retrieved successfully.",
        )

    async def search_wallets(
        self,
        query: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        wallet_type: str | None = None,
        currency: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet search requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="search_wallets",
            handler=self._resolve_handler("search_wallets"),
            payload={
                "admin_id": target_admin_id,
                "query": query,
                "page": page,
                "page_size": page_size,
                "status": status,
                "wallet_type": wallet_type,
                "currency": currency,
                "order_by": order_by,
                "descending": descending,
            },
            success_message="Wallet search completed successfully.",
        )

    async def wallet_history(
        self,
        wallet_id: UUID,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_desc: bool = True,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet transaction history requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="wallet_history",
            handler=self._resolve_handler("wallet_history"),
            payload={
                "admin_id": target_admin_id,
                "wallet_id": wallet_id,
                "page": page,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_desc": sort_desc,
            },
            success_message="Wallet history retrieved successfully.",
        )

    async def wallet_analytics(
        self,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet analytics requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="wallet_analytics",
            handler=self._resolve_handler("wallet_analytics"),
            payload={
                "admin_id": target_admin_id,
                "wallet_id": wallet_id,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
                "transaction_type": transaction_type,
            },
            success_message="Wallet analytics retrieved successfully.",
        )

    async def freeze_wallet(
        self,
        wallet_id: UUID,
        payload: WalletStateChangeRequest | None = None,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet freeze requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="freeze_wallet",
            handler=self._resolve_handler("freeze_wallet"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id, **request_payload},
            success_message="Wallet frozen successfully.",
        )

    async def unfreeze_wallet(self, wallet_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle wallet unfreeze requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="unfreeze_wallet",
            handler=self._resolve_handler("unfreeze_wallet"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id},
            success_message="Wallet unfrozen successfully.",
        )

    async def lock_wallet(
        self,
        wallet_id: UUID,
        payload: WalletFundsMutationRequest,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet lock requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="lock_wallet",
            handler=self._resolve_handler("lock_wallet"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id, **payload.model_dump(exclude_unset=True)},
            success_message="Wallet funds locked successfully.",
        )

    async def unlock_wallet(
        self,
        wallet_id: UUID,
        payload: WalletFundsMutationRequest,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet unlock requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="unlock_wallet",
            handler=self._resolve_handler("unlock_wallet"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id, **payload.model_dump(exclude_unset=True)},
            success_message="Wallet funds unlocked successfully.",
        )

    async def reconcile_wallet(
        self,
        wallet_id: UUID,
        payload: WalletReconciliationRequest | None = None,
        admin_id: UUID | None = None,
        request: Request | None = None,
    ) -> dict[str, Any]:
        """Handle wallet reconciliation requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="reconcile_wallet",
            handler=self._resolve_handler("reconcile_wallet"),
            payload={"admin_id": target_admin_id, "wallet_id": wallet_id, **request_payload},
            success_message="Wallet reconciliation completed successfully.",
        )

    async def list_transactions(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction monitoring requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="list_transactions",
            handler=self._resolve_handler("list_transactions"),
            payload={"admin_id": target_admin_id},
            success_message="Transactions retrieved successfully.",
        )

    async def reverse_transaction(self, transaction_id: UUID, payload: TransactionReverseRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction reversal requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
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

    async def get_transaction_details(self, transaction_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction detail retrieval requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_transaction_details",
            handler=self._resolve_handler("get_transaction_details"),
            payload={"admin_id": target_admin_id, "transaction_id": transaction_id},
            success_message="Transaction retrieved successfully.",
        )

    async def retry_failed_transaction(self, transaction_id: UUID, payload: TransactionRetryRequest | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle retry requests for failed transactions."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="retry_failed_transaction",
            handler=self._resolve_handler("retry_failed_transaction"),
            payload={"admin_id": target_admin_id, "transaction_id": transaction_id, **request_payload},
            success_message="Transaction retry completed successfully.",
        )

    async def resolve_transaction(self, transaction_id: UUID, payload: TransactionResolveRequest | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction resolution requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="resolve_transaction",
            handler=self._resolve_handler("resolve_transaction"),
            payload={"admin_id": target_admin_id, "transaction_id": transaction_id, **request_payload},
            success_message="Transaction resolution completed successfully.",
        )

    async def transaction_timeline(self, transaction_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle transaction timeline requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="transaction_timeline",
            handler=self._resolve_handler("transaction_timeline"),
            payload={"admin_id": target_admin_id, "transaction_id": transaction_id},
            success_message="Transaction timeline retrieved successfully.",
        )

    async def list_providers(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle provider management listing requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="list_providers",
            handler=self._resolve_handler("list_providers"),
            payload={"admin_id": target_admin_id},
            success_message="Providers retrieved successfully.",
        )

    async def configure_provider(self, provider_id: UUID, payload: ProviderManagementRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle provider configuration requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
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

    async def get_system_settings(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle system settings retrieval requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_system_settings",
            handler=self._resolve_handler("get_system_settings"),
            payload={"admin_id": target_admin_id},
            success_message="System settings retrieved successfully.",
        )

    async def update_system_settings(self, payload: SystemSettingsRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle system settings update requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="update_system_settings",
            handler=self._resolve_handler("update_system_settings"),
            payload={"admin_id": target_admin_id, "updates": payload.updates},
            success_message="System settings updated successfully.",
        )

    async def list_audit_logs(self, payload: AuditLogFilterSchema | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle audit log requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        request_payload = payload.model_dump(exclude_unset=True) if payload else {}
        return await self._execute(
            action="list_audit_logs",
            handler=self._resolve_handler("list_audit_logs"),
            payload={"admin_id": target_admin_id, **request_payload},
            success_message="Audit logs retrieved successfully.",
        )

    async def create_api_key(self, payload: APIKeyCreate, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle API key creation requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="create_api_key",
            handler=self._resolve_handler("create_api_key"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="API key created successfully.",
        )

    async def update_api_key(self, api_key_id: UUID, payload: APIKeyUpdate, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle API key update requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="update_api_key",
            handler=self._resolve_handler("update_api_key"),
            payload={"admin_id": target_admin_id, "key_id": str(api_key_id), **payload.model_dump(exclude_unset=True)},
            success_message="API key updated successfully.",
        )

    async def revoke_api_key(self, api_key_id: UUID, payload: APIKeyRevokeSchema, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle API key revocation requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="revoke_api_key",
            handler=self._resolve_handler("revoke_api_key"),
            payload={"admin_id": target_admin_id, "key_id": str(api_key_id), **payload.model_dump(exclude_unset=True)},
            success_message="API key revoked successfully.",
        )

    async def broadcast_notification(self, payload: NotificationBroadcastRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle notification broadcast requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="broadcast_notification",
            handler=self._resolve_handler("broadcast_notification"),
            payload={
                "admin_id": target_admin_id,
                **payload.model_dump(exclude_unset=True),
            },
            success_message="Notification broadcast completed successfully.",
        )

    async def get_platform_statistics(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle platform statistics requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_platform_statistics",
            handler=self._resolve_handler("get_platform_statistics"),
            payload={"admin_id": target_admin_id},
            success_message="Platform statistics retrieved successfully.",
        )

    async def get_service_monitoring(self, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle service monitoring requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_service_monitoring",
            handler=self._resolve_handler("get_service_monitoring"),
            payload={"admin_id": target_admin_id},
            success_message="Service monitoring data retrieved successfully.",
        )

    async def generate_report(self, payload: ReportGenerationRequest, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle report generation requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="generate_report",
            handler=self._resolve_handler("generate_report"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="Report generated successfully.",
        )

    async def create_admin_account(self, payload: AdminCreate, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle administrator account creation requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="create_admin_account",
            handler=self._resolve_handler("create_admin_account"),
            payload={"admin_id": target_admin_id, **payload.model_dump(exclude_unset=True)},
            success_message="Admin account created successfully.",
        )

    async def update_admin_account(self, admin_id_value: UUID, payload: AdminUpdate, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle administrator account update requests."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="update_admin_account",
            handler=self._resolve_handler("update_admin_account"),
            payload={"admin_id": str(admin_id_value), "performed_by_admin_id": str(target_admin_id), **payload.model_dump(exclude_unset=True)},
            success_message="Admin account updated successfully.",
        )

    async def revoke_user_session(self, session_id: str, reason: str | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle revocation of a specific user session."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="revoke_user_session",
            handler=self._resolve_handler("revoke_user_session"),
            payload={"admin_id": target_admin_id, "session_id": session_id, "reason": reason},
            success_message="Session revoked successfully.",
        )

    async def list_user_sessions(self, user_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle listing active sessions for a user."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="list_user_sessions",
            handler=self._resolve_handler("list_user_sessions"),
            payload={"admin_id": target_admin_id, "user_id": user_id},
            success_message="User sessions retrieved successfully.",
        )

    async def revoke_all_user_sessions(self, user_id: UUID, reason: str | None = None, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle revoking all active sessions for a user."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="revoke_all_user_sessions",
            handler=self._resolve_handler("revoke_all_user_sessions"),
            payload={"admin_id": target_admin_id, "user_id": user_id, "reason": reason},
            success_message="All user sessions revoked successfully.",
        )

    async def get_active_session_count(self, user_id: UUID, admin_id: UUID | None = None, request: Request | None = None) -> dict[str, Any]:
        """Handle retrieval of active session count for a user."""
        target_admin_id = self._resolve_effective_admin_id(admin_id=admin_id, request=request)
        return await self._execute(
            action="get_active_session_count",
            handler=self._resolve_handler("get_active_session_count"),
            payload={"admin_id": target_admin_id, "user_id": user_id},
            success_message="Active session count retrieved successfully.",
        )

    def _resolve_effective_admin_id(self, *, admin_id: UUID | None, request: Request | None) -> UUID:
        authenticated_admin_id = self._resolve_authenticated_admin_id(request)
        if authenticated_admin_id is not None:
            return authenticated_admin_id
        if admin_id is not None:
            return admin_id
        return self._resolve_admin_id()

    def _resolve_authenticated_admin_id(self, request: Request | None) -> UUID | None:
        if request is None:
            return None
        auth_user = getattr(request.state, "auth_user", None)
        raw_admin_id = getattr(auth_user, "user_id", None) if auth_user is not None else None
        if raw_admin_id is None:
            auth_payload = getattr(request.state, "auth_payload", None)
            if isinstance(auth_payload, dict):
                raw_admin_id = auth_payload.get("user_id") or auth_payload.get("sub")
        if raw_admin_id is None:
            return None
        try:
            return UUID(str(raw_admin_id))
        except (ValueError, TypeError):
            return None

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
