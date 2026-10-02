from __future__ import annotations

"""Admin route registration for the CosmozPay backend."""

from typing import Any
from uuid import UUID
import importlib

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers.admin_controller import (
    AdminController,
    AdminDashboardRequest,
    NotificationBroadcastRequest,
    ProviderManagementRequest,
    ReportGenerationRequest,
    TransactionResolveRequest,
    TransactionRetryRequest,
    SystemSettingsRequest,
    TransactionReverseRequest,
    UserManagementRequest,
    WalletAdjustmentRequest,
    WalletFundsMutationRequest,
    WalletReconciliationRequest,
    WalletStateChangeRequest,
)
from app.repositories.admin_repository import AdminRepository
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.kyc_repository import KYCRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.provider_repository import ProviderRepository
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.user_repository import UserRepository
from app.routes.airtime_routes import get_airtime_service
from app.routes.data_routes import get_data_service
from app.routes.education_routes import get_education_service
from app.routes.electricity_routes import get_electricity_service
from app.routes.payment_routes import get_payment_service
from app.routes.tv_routes import get_tv_service
from app.routes.wallet_routes import get_wallet_service
from app.schemas.admin_schema import AdminCreate, AdminUpdate
from app.schemas.api_key_schema import APIKeyCreate, APIKeyRevokeSchema, APIKeyUpdate
from app.schemas.audit_log_schema import AuditLogFilterSchema
from app.schemas.kyc_schema import KYCApprovalSchema
from app.services.notification_service import build_notification_service
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
from app.services.account_state import UserAccountStateService
from app.services.auth.password_service import PasswordService
from app.services.auth.session_service import SessionService
from app.services.security.account_lockout import AccountLockoutService
from app.services.transaction_owner import TransactionOwnerResolver
from app.services.provider.health import ProviderHealthService
from app.services.provider.selector import ProviderSelector
from app.services.user.kyc import KYCService as UserKYCService
from app.services.wallet.statement import WalletStatementService
from app.services.wallet.wallet_balance import WalletBalanceService
from app.services.wallet.wallet_reconciliation import WalletReconciliationService
from app.services.wallet.withdrawal import WalletWithdrawalService


router = APIRouter(prefix="/admin", tags=["Admin"])


async def get_admin_service(session: AsyncSession = Depends(get_db)) -> AdminService:
    """Compose the admin service graph per request using the active database session."""
    logger = get_logger("api")
    stage = "admin_factory_start"
    try:
        logger.info("admin_factory_start")
        session_service = SessionService(logger=logger)
        password_service = PasswordService(logger=logger)
        stage = "admin_factory_repositories_created"
        logger.info("admin_factory_repositories_created")
        admin_repository = AdminRepository(session=session)
        audit_repository = AuditLogRepository(session=session)
        settings_repository = SystemSettingsRepository(session=session)
        provider_repository = ProviderRepository(session=session)
        provider_selector = ProviderSelector(provider_repository=provider_repository)
        provider_health_service = ProviderHealthService(provider_repository=provider_repository)
        notification_repository = NotificationRepository(session=session)
        user_repository = UserRepository(session=session)
        kyc_repository = KYCRepository(session=session)
        transaction_repository = TransactionRepository(session=session)
        wallet_repository = WalletRepository(session=session)
        stage = "admin_factory_basic_services_created"
        logger.info("admin_factory_basic_services_created")
        notification_service = build_notification_service(session=session)
        stage = "admin_factory_notification_service_created"
        logger.info("admin_factory_notification_service_created")
        api_key_repository = APIKeyRepository(session=session)
        ledger_repository = None
        ledger_service = None
        ledger_repository_module = importlib.import_module("app.repositories.ledger_repository")
        ledger_service_module = importlib.import_module("app.services.ledger_service")
        ledger_repository_cls = getattr(ledger_repository_module, "LedgerRepository", None)
        ledger_service_cls = getattr(ledger_service_module, "LedgerService", None)
        if ledger_repository_cls is not None and ledger_service_cls is not None:
            ledger_repository = ledger_repository_cls(session=session)
            ledger_service = ledger_service_cls(
                ledger_repository=ledger_repository,
                wallet_repository=wallet_repository,
                user_repository=user_repository,
                transaction_repository=transaction_repository,
                notification_service=notification_service,
                logger=logger,
            )
        stage = "admin_factory_ledger_created"
        logger.info("admin_factory_ledger_created")
        stage = "admin_factory_wallet_service_created"
        logger.info("admin_factory_wallet_service_created")
        wallet_service = await get_wallet_service(session=session)
        stage = "admin_factory_payment_service_created"
        logger.info("admin_factory_payment_service_created")
        payment_service = await get_payment_service(session=session)
        stage = "admin_factory_airtime_service_created"
        logger.info("admin_factory_airtime_service_created")
        airtime_service = await get_airtime_service(session=session)
        stage = "admin_factory_data_service_created"
        logger.info("admin_factory_data_service_created")
        data_service = await get_data_service(session=session)
        stage = "admin_factory_electricity_service_created"
        logger.info("admin_factory_electricity_service_created")
        electricity_service = await get_electricity_service(session=session)
        stage = "admin_factory_tv_service_created"
        logger.info("admin_factory_tv_service_created")
        tv_service = await get_tv_service(session=session)
        stage = "admin_factory_education_service_created"
        logger.info("admin_factory_education_service_created")
        education_service = await get_education_service(session=session)
        wallet_withdrawal_service = WalletWithdrawalService(
            wallet_repository=wallet_repository,
            transaction_repository=transaction_repository,
            session=session,
        )
        account_lockout_service = AccountLockoutService(user_repository=user_repository, logger=logger)
        account_state_service = UserAccountStateService()
        transaction_owner_resolver = TransactionOwnerResolver()
        kyc_domain_service = UserKYCService(
            user_repository=user_repository,
            kyc_repository=kyc_repository,
            session=session,
            logger=logger,
        )
        wallet_balance_service = None
        if ledger_service is not None:
            wallet_balance_service = WalletBalanceService(
                wallet_repository=wallet_repository,
                ledger_service=ledger_service,
                session=session,
                logger=logger,
            )
        wallet_statement_service = WalletStatementService(
            wallet_repository=wallet_repository,
            transaction_repository=transaction_repository,
            session=session,
            logger=logger,
        )
        wallet_reconciliation_service = WalletReconciliationService(
            wallet_repository=wallet_repository,
            transaction_repository=transaction_repository,
            session=session,
            logger=logger,
        )

        stage = "admin_factory_admin_services_created"
        logger.info("admin_factory_admin_services_created")
        admin_service = AdminService(
            admin_repository=admin_repository,
            dashboard_service=DashboardService(
                logger=logger,
                user_repository=user_repository,
                transaction_repository=transaction_repository,
                kyc_repository=kyc_repository,
                ledger_service=ledger_service,
            ),
            user_service=UserAdministrationService(
                logger=logger,
                user_repository=user_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                notification_service=notification_service,
                account_lockout_service=account_lockout_service,
                account_state_service=account_state_service,
                session=session,
            ),
            kyc_service=KYCService(
                logger=logger,
                kyc_domain_service=kyc_domain_service,
                notification_service=notification_service,
                audit_repository=audit_repository,
                session=session,
            ),
            wallet_service=WalletAdministrationService(
                logger=logger,
                wallet_repository=wallet_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                notification_service=notification_service,
                wallet_service=wallet_service,
                wallet_balance_service=wallet_balance_service,
                wallet_statement_service=wallet_statement_service,
                wallet_reconciliation_service=wallet_reconciliation_service,
                session=session,
            ),
            transaction_service=TransactionAdministrationService(
                logger=logger,
                transaction_repository=transaction_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                notification_service=notification_service,
                wallet_funding_service=wallet_service.funding_service,
                wallet_transfer_service=wallet_service.transfer_service,
                wallet_withdrawal_service=wallet_withdrawal_service,
                airtime_service=airtime_service,
                data_service=data_service,
                electricity_service=electricity_service,
                tv_service=tv_service,
                education_service=education_service,
                payment_service=payment_service,
                transaction_owner_resolver=transaction_owner_resolver,
                session=session,
            ),
            payment_service=PaymentAdministrationService(logger=logger),
            finance_service=FinanceService(
                logger=logger,
                ledger_service=ledger_service,
                transaction_repository=transaction_repository,
                provider_repository=provider_repository,
            ),
            fraud_service=FraudService(logger=logger),
            provider_service=ProviderAdministrationService(
                logger=logger,
                provider_repository=provider_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                provider_health_service=provider_health_service,
                provider_selector=provider_selector,
                session=session,
            ),
            settings_service=SettingsService(
                logger=logger,
                settings_repository=settings_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                session=session,
            ),
            audit_service=AuditService(logger=logger, audit_repository=audit_repository),
            api_key_service=ApiKeyService(api_key_repository=api_key_repository, logger=logger),
            notification_service=NotificationAdministrationService(
                logger=logger,
                notification_repository=notification_repository,
                notification_service=notification_service,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                session=session,
            ),
            monitoring_service=MonitoringService(
                logger=logger,
                provider_repository=provider_repository,
                provider_health_service=provider_health_service,
            ),
            report_service=ReportService(
                logger=logger,
                audit_repository=audit_repository,
                ledger_service=ledger_service,
                transaction_repository=transaction_repository,
                provider_repository=provider_repository,
                provider_health_service=provider_health_service,
            ),
            staff_service=StaffAdministrationService(
                logger=logger,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                password_service=password_service,
                session=session,
            ),
            support_service=SupportAdministrationService(logger=logger),
            security_service=SecurityAdministrationService(logger=logger),
            session_service=SessionAdministrationService(
                logger=logger,
                session_service=session_service,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
            ),
            product_service=ProductAdministrationService(logger=logger),
            marketing_service=MarketingService(logger=logger),
            merchant_service=MerchantService(logger=logger),
            logger=logger,
        )
        logger.info("admin_factory_complete")
        return admin_service
    except Exception as exc:
        logger.warning(
            "admin_factory_failed stage=%s exception_type=%s exception_message=[redacted]",
            stage,
            type(exc).__name__,
        )
        raise
async def get_admin_controller(
    admin_service: AdminService = Depends(get_admin_service),
) -> AdminController:
    """Instantiate the admin controller with a request-scoped admin service."""
    return AdminController(admin_service)


@router.get("/dashboard", status_code=status.HTTP_200_OK)
async def get_dashboard(
    request: Request,
    payload: AdminDashboardRequest | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_dashboard(payload, request=request)


@router.get("/me", status_code=status.HTTP_200_OK)
async def get_current_admin(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_me(request=request)


@router.get("/users", status_code=status.HTTP_200_OK)
async def list_users(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_users(request=request)


@router.get("/users/{user_id}", status_code=status.HTTP_200_OK)
async def get_user(
    user_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user(UUID(user_id), request=request)


@router.post("/users/{user_id}/manage", status_code=status.HTTP_200_OK)
async def manage_user(
    user_id: str,
    payload: UserManagementRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.manage_user(UUID(user_id), payload, request=request)


@router.post("/kyc/{user_id}/review", status_code=status.HTTP_200_OK)
async def review_kyc(
    user_id: str,
    payload: KYCApprovalSchema,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.review_kyc(UUID(user_id), payload, request=request)


@router.post("/wallets/adjust", status_code=status.HTTP_200_OK)
async def adjust_wallet(
    payload: WalletAdjustmentRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.adjust_wallet(payload, request=request)


@router.get("/wallets", status_code=status.HTTP_200_OK)
async def list_wallets(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    wallet_type: str | None = None,
    currency: str | None = None,
    query: str | None = None,
    order_by: str = "created_at",
    descending: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_wallets(
        page=page,
        page_size=page_size,
        status=status,
        wallet_type=wallet_type,
        currency=currency,
        query=query,
        order_by=order_by,
        descending=descending,
        request=request,
    )


@router.get("/wallets/search", status_code=status.HTTP_200_OK)
async def search_wallets(
    query: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    wallet_type: str | None = None,
    currency: str | None = None,
    order_by: str = "created_at",
    descending: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.search_wallets(
        query=query,
        page=page,
        page_size=page_size,
        status=status,
        wallet_type=wallet_type,
        currency=currency,
        order_by=order_by,
        descending=descending,
        request=request,
    )


@router.get("/wallets/{wallet_id}", status_code=status.HTTP_200_OK)
async def get_wallet_details(
    wallet_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_wallet_details(UUID(wallet_id), request=request)


@router.get("/wallets/user/{user_id}", status_code=status.HTTP_200_OK)
async def get_user_wallet(
    user_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_wallet(UUID(user_id), request=request)


@router.get("/wallets/{wallet_id}/history", status_code=status.HTTP_200_OK)
async def wallet_history(
    wallet_id: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "created_at",
    sort_desc: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.wallet_history(
        wallet_id=UUID(wallet_id),
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_desc=sort_desc,
        request=request,
    )


@router.get("/wallets/{wallet_id}/analytics", status_code=status.HTTP_200_OK)
async def wallet_analytics(
    wallet_id: str,
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    transaction_type: str | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.wallet_analytics(
        wallet_id=UUID(wallet_id),
        start_date=start_date,
        end_date=end_date,
        status=status,
        transaction_type=transaction_type,
        request=request,
    )


@router.post("/wallets/{wallet_id}/freeze", status_code=status.HTTP_200_OK)
async def freeze_wallet(
    wallet_id: str,
    request: Request,
    payload: WalletStateChangeRequest | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.freeze_wallet(UUID(wallet_id), payload, request=request)


@router.post("/wallets/{wallet_id}/unfreeze", status_code=status.HTTP_200_OK)
async def unfreeze_wallet(
    wallet_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.unfreeze_wallet(UUID(wallet_id), request=request)


@router.post("/wallets/{wallet_id}/lock", status_code=status.HTTP_200_OK)
async def lock_wallet(
    wallet_id: str,
    payload: WalletFundsMutationRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.lock_wallet(UUID(wallet_id), payload, request=request)


@router.post("/wallets/{wallet_id}/unlock", status_code=status.HTTP_200_OK)
async def unlock_wallet(
    wallet_id: str,
    payload: WalletFundsMutationRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.unlock_wallet(UUID(wallet_id), payload, request=request)


@router.post("/wallets/{wallet_id}/reconcile", status_code=status.HTTP_200_OK)
async def reconcile_wallet(
    wallet_id: str,
    request: Request,
    payload: WalletReconciliationRequest | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.reconcile_wallet(UUID(wallet_id), payload, request=request)


@router.get("/transactions", status_code=status.HTTP_200_OK)
async def list_transactions(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_transactions(request=request)


@router.get("/transactions/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_transaction_details(
    transaction_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_transaction_details(UUID(transaction_id), request=request)


@router.post("/transactions/{transaction_id}/reverse", status_code=status.HTTP_200_OK)
async def reverse_transaction(
    transaction_id: str,
    payload: TransactionReverseRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.reverse_transaction(UUID(transaction_id), payload, request=request)


@router.post("/transactions/{transaction_id}/retry", status_code=status.HTTP_200_OK)
async def retry_failed_transaction(
    transaction_id: str,
    request: Request,
    payload: TransactionRetryRequest | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.retry_failed_transaction(UUID(transaction_id), payload, request=request)


@router.post("/transactions/{transaction_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_transaction(
    transaction_id: str,
    request: Request,
    payload: TransactionResolveRequest | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.resolve_transaction(UUID(transaction_id), payload, request=request)


@router.get("/transactions/{transaction_id}/timeline", status_code=status.HTTP_200_OK)
async def transaction_timeline(
    transaction_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.transaction_timeline(UUID(transaction_id), request=request)


@router.get("/providers", status_code=status.HTTP_200_OK)
async def list_providers(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_providers(request=request)


@router.post("/providers/{provider_id}/configure", status_code=status.HTTP_200_OK)
async def configure_provider(
    provider_id: str,
    payload: ProviderManagementRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.configure_provider(UUID(provider_id), payload, request=request)


@router.get("/settings", status_code=status.HTTP_200_OK)
async def get_system_settings(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_system_settings(request=request)


@router.put("/settings", status_code=status.HTTP_200_OK)
async def update_system_settings(
    payload: SystemSettingsRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_system_settings(payload, request=request)


@router.get("/audit-logs", status_code=status.HTTP_200_OK)
async def list_audit_logs(
    request: Request,
    payload: AuditLogFilterSchema | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_audit_logs(payload, request=request)


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: APIKeyCreate,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.create_api_key(payload, request=request)


@router.put("/api-keys/{api_key_id}", status_code=status.HTTP_200_OK)
async def update_api_key(
    api_key_id: str,
    payload: APIKeyUpdate,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_api_key(UUID(api_key_id), payload, request=request)


@router.post("/api-keys/{api_key_id}/revoke", status_code=status.HTTP_200_OK)
async def revoke_api_key(
    api_key_id: str,
    payload: APIKeyRevokeSchema,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_api_key(UUID(api_key_id), payload, request=request)


@router.post("/notifications/broadcast", status_code=status.HTTP_201_CREATED)
async def broadcast_notification(
    payload: NotificationBroadcastRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.broadcast_notification(payload, request=request)


@router.get("/statistics", status_code=status.HTTP_200_OK)
async def get_platform_statistics(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_platform_statistics(request=request)


@router.get("/monitoring", status_code=status.HTTP_200_OK)
async def get_service_monitoring(
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_service_monitoring(request=request)


@router.post("/reports", status_code=status.HTTP_200_OK)
async def generate_report(
    payload: ReportGenerationRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.generate_report(payload, request=request)


@router.post("/admins", status_code=status.HTTP_201_CREATED)
async def create_admin_account(
    payload: AdminCreate,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.create_admin_account(payload, request=request)


@router.put("/admins/{admin_id}", status_code=status.HTTP_200_OK)
async def update_admin_account(
    admin_id: str,
    payload: AdminUpdate,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.update_admin_account(UUID(admin_id), payload, request=request)


@router.post("/sessions/{session_id}/revoke", status_code=status.HTTP_200_OK)
async def revoke_user_session(
    session_id: str,
    request: Request,
    reason: str | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_user_session(session_id=session_id, reason=reason, request=request)


@router.get("/users/{user_id}/sessions", status_code=status.HTTP_200_OK)
async def list_user_sessions(
    user_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_user_sessions(UUID(user_id), request=request)


@router.post("/users/{user_id}/sessions/revoke-all", status_code=status.HTTP_200_OK)
async def revoke_all_user_sessions(
    user_id: str,
    request: Request,
    reason: str | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_all_user_sessions(UUID(user_id), reason=reason, request=request)


@router.get("/users/{user_id}/sessions/count", status_code=status.HTTP_200_OK)
async def get_active_session_count(
    user_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_active_session_count(UUID(user_id), request=request)
