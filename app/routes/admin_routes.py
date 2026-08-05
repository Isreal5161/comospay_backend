from __future__ import annotations

"""Admin route registration for the CosmozPay backend."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.admin_controller import AdminController
from app.services.admin import (
    ApiKeyService,
    AuditService,
    DashboardService,
    FinanceService,
    FraudService,
    KYCService,
    MarketingService,
    MerchantService,
    MonitoringService,
    NotificationAdministrationService,
    PaymentAdministrationService,
    ProductAdministrationService,
    ProviderAdministrationService,
    ReportService,
    SecurityAdministrationService,
    SessionAdministrationService,
    SettingsService,
    StaffAdministrationService,
    SupportAdministrationService,
    TransactionAdministrationService,
    UserAdministrationService,
    WalletAdministrationService,
)
from app.services.admin_service import AdminService
from app.services.auth.session_service import SessionService


router = APIRouter(prefix="/admin", tags=["Admin"])


async def get_admin_service(session: AsyncSession = Depends(get_db)) -> AdminService:
    """Compose the admin service graph per request using the active database session."""
    logger = None
    session_service = SessionService(logger=logger)

    admin_service = AdminService(logger=logger)
    admin_service.dashboard_service = DashboardService(logger=logger)
    admin_service.user_service = UserAdministrationService(logger=logger)
    admin_service.kyc_service = KYCService(logger=logger)
    admin_service.wallet_service = WalletAdministrationService(logger=logger)
    admin_service.transaction_service = TransactionAdministrationService(logger=logger)
    admin_service.payment_service = PaymentAdministrationService(logger=logger)
    admin_service.finance_service = FinanceService(logger=logger)
    admin_service.fraud_service = FraudService(logger=logger)
    admin_service.provider_service = ProviderAdministrationService(logger=logger)
    admin_service.settings_service = SettingsService(logger=logger)
    admin_service.audit_service = AuditService(logger=logger)
    admin_service.api_key_service = ApiKeyService(logger=logger)
    admin_service.notification_service = NotificationAdministrationService(logger=logger)
    admin_service.monitoring_service = MonitoringService(logger=logger)
    admin_service.report_service = ReportService(logger=logger)
    admin_service.staff_service = StaffAdministrationService(logger=logger)
    admin_service.support_service = SupportAdministrationService(logger=logger)
    admin_service.security_service = SecurityAdministrationService(logger=logger)
    admin_service.session_service = SessionAdministrationService(logger=logger, session_service=session_service)
    admin_service.product_service = ProductAdministrationService(logger=logger)
    admin_service.marketing_service = MarketingService(logger=logger)
    admin_service.merchant_service = MerchantService(logger=logger)

    return admin_service


async def get_admin_controller(
    admin_service: AdminService = Depends(get_admin_service),
) -> AdminController:
    """Instantiate the admin controller with a request-scoped admin service."""
    return AdminController(admin_service)


@router.get("/dashboard", status_code=status.HTTP_200_OK)
async def get_dashboard(
    payload: Any | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_dashboard(payload)


@router.get("/users", status_code=status.HTTP_200_OK)
async def list_users(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_users()


@router.get("/users/{user_id}", status_code=status.HTTP_200_OK)
async def get_user(
    user_id: str,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user(UUID(user_id))


@router.post("/users/{user_id}/manage", status_code=status.HTTP_200_OK)
async def manage_user(
    user_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.manage_user(UUID(user_id), payload)


@router.post("/kyc/{user_id}/review", status_code=status.HTTP_200_OK)
async def review_kyc(
    user_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.review_kyc(UUID(user_id), payload)


@router.post("/wallets/adjust", status_code=status.HTTP_200_OK)
async def adjust_wallet(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.adjust_wallet(payload)


@router.get("/transactions", status_code=status.HTTP_200_OK)
async def list_transactions(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_transactions()


@router.post("/transactions/{transaction_id}/reverse", status_code=status.HTTP_200_OK)
async def reverse_transaction(
    transaction_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.reverse_transaction(UUID(transaction_id), payload)


@router.get("/providers", status_code=status.HTTP_200_OK)
async def list_providers(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_providers()


@router.post("/providers/{provider_id}/configure", status_code=status.HTTP_200_OK)
async def configure_provider(
    provider_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.configure_provider(UUID(provider_id), payload)


@router.get("/settings", status_code=status.HTTP_200_OK)
async def get_system_settings(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_system_settings()


@router.put("/settings", status_code=status.HTTP_200_OK)
async def update_system_settings(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_system_settings(payload)


@router.get("/audit-logs", status_code=status.HTTP_200_OK)
async def list_audit_logs(
    payload: Any | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_audit_logs(payload)


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.create_api_key(payload)


@router.put("/api-keys/{api_key_id}", status_code=status.HTTP_200_OK)
async def update_api_key(
    api_key_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_api_key(UUID(api_key_id), payload)


@router.post("/api-keys/{api_key_id}/revoke", status_code=status.HTTP_200_OK)
async def revoke_api_key(
    api_key_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_api_key(UUID(api_key_id), payload)


@router.post("/notifications/broadcast", status_code=status.HTTP_201_CREATED)
async def broadcast_notification(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.broadcast_notification(payload)


@router.get("/statistics", status_code=status.HTTP_200_OK)
async def get_platform_statistics(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_platform_statistics()


@router.get("/monitoring", status_code=status.HTTP_200_OK)
async def get_service_monitoring(
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_service_monitoring()


@router.post("/reports", status_code=status.HTTP_200_OK)
async def generate_report(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.generate_report(payload)


@router.post("/admins", status_code=status.HTTP_201_CREATED)
async def create_admin_account(
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.create_admin_account(payload)


@router.put("/admins/{admin_id}", status_code=status.HTTP_200_OK)
async def update_admin_account(
    admin_id: str,
    payload: Any,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_admin_account(UUID(admin_id), payload)
