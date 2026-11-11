from __future__ import annotations

"""Admin route registration for the CosmozPay backend."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID
import importlib

from fastapi import APIRouter, Depends, Query, Request, status
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
from app.repositories.platform_income_repository import PlatformIncomeRepository
from app.repositories.provider_acquisition_cost_repository import ProviderAcquisitionCostRepository
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
from app.schemas.admin_schema import (
    AdminCreate,
    AdminUpdate,
    AdminUserDetailResponse,
    AdminUserListResponse,
    AdminUserManagementResponse,
    DashboardResponse,
)
from app.schemas.api_key_schema import APIKeyCreate, APIKeyRevokeSchema, APIKeyUpdate
from app.schemas.audit_log_schema import AuditLogFilterSchema
from app.schemas.kyc_schema import KYCApprovalSchema
from app.schemas.provider_acquisition_cost_schema import (
    ProviderAcquisitionCostCreate,
    ProviderAcquisitionCostUpdate,
)
from app.services.notification_service import build_notification_service
from app.utils.logger import get_logger
from app.utils.response import success_response
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
from app.services.provider_acquisition_cost_service import (
    ProviderAcquisitionCostAdministrationService,
)
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


async def get_provider_cost_administration_service(
    session: AsyncSession = Depends(get_db),
) -> ProviderAcquisitionCostAdministrationService:
    repository = ProviderAcquisitionCostRepository(session)
    return ProviderAcquisitionCostAdministrationService(session, repository)


def _provider_cost_admin_identity(request: Request, *, write: bool = False) -> tuple[UUID, str]:
    user = getattr(request.state, "auth_user", None) or getattr(request.state, "user", None)
    if user is None or getattr(user, "identity_type", None) != "admin":
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="An administrator identity is required.")
    role = str(getattr(user, "role", "")).strip().lower().replace(" ", "_")
    if write and role not in {"admin", "super_admin"}:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Only administrators can change provider acquisition costs.")
    try:
        admin_id = UUID(str(getattr(user, "user_id", "")))
    except (ValueError, TypeError) as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Administrator identity is invalid.") from exc
    return admin_id, role


def _provider_cost_error(exc: ValueError) -> None:
    from fastapi import HTTPException

    message = str(exc)
    status_code = 404 if "was not found" in message.lower() else 422
    raise HTTPException(status_code=status_code, detail=message) from exc


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
        platform_income_repository = PlatformIncomeRepository(session=session)
        provider_cost_repository = ProviderAcquisitionCostRepository(session=session)
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
            ledger_service=ledger_service,
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
        transaction_administration_service = TransactionAdministrationService(
            logger=logger,
            transaction_repository=transaction_repository,
            ledger_repository=ledger_repository,
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
        )
        admin_service = AdminService(
            admin_repository=admin_repository,
            dashboard_service=DashboardService(
                logger=logger,
                user_repository=user_repository,
                transaction_repository=transaction_repository,
                kyc_repository=kyc_repository,
                wallet_repository=wallet_repository,
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
                session_service=session_service,
                session=session,
            ),
            kyc_service=KYCService(
                logger=logger,
                kyc_domain_service=kyc_domain_service,
                kyc_repository=kyc_repository,
                admin_repository=admin_repository,
                notification_service=notification_service,
                audit_repository=audit_repository,
                session=session,
            ),
            wallet_service=WalletAdministrationService(
                logger=logger,
                wallet_repository=wallet_repository,
                ledger_repository=ledger_repository,
                admin_repository=admin_repository,
                audit_repository=audit_repository,
                notification_service=notification_service,
                wallet_service=wallet_service,
                wallet_balance_service=wallet_balance_service,
                wallet_statement_service=wallet_statement_service,
                wallet_reconciliation_service=wallet_reconciliation_service,
                session=session,
            ),
            transaction_service=transaction_administration_service,
            payment_service=PaymentAdministrationService(
                transaction_service=transaction_administration_service,
                wallet_repository=wallet_repository,
                user_repository=user_repository,
                logger=logger,
            ),
            finance_service=FinanceService(
                logger=logger,
                ledger_service=ledger_service,
                transaction_repository=transaction_repository,
                provider_repository=provider_repository,
                platform_income_repository=platform_income_repository,
                provider_cost_repository=provider_cost_repository,
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


@router.get("/dashboard", status_code=status.HTTP_200_OK, response_model=DashboardResponse)
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


@router.get(
    "/users",
    status_code=status.HTTP_200_OK,
    response_model=AdminUserListResponse,
)
async def list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=50),
    sort_by: Literal[
        "first_name",
        "last_name",
        "email",
        "username",
        "phone",
        "status",
        "created_at",
        "updated_at",
        "last_login_at",
    ] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_users(
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
        request=request,
    )


@router.get(
    "/users/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=AdminUserDetailResponse,
)
async def get_user(
    user_id: UUID,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user(user_id, request=request)


@router.get("/users/{user_id}/activity", status_code=status.HTTP_200_OK)
async def get_user_activity(
    user_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_activity(
        user_id,
        page=page,
        page_size=page_size,
        request=request,
    )


@router.post(
    "/users/{user_id}/manage",
    status_code=status.HTTP_200_OK,
    response_model=AdminUserManagementResponse,
)
async def manage_user(
    user_id: UUID,
    payload: UserManagementRequest,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.manage_user(user_id, payload, request=request)


@router.get("/users/{user_id}/kyc", status_code=status.HTTP_200_OK)
async def get_user_kyc(
    user_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_kyc(
        user_id,
        page=page,
        page_size=page_size,
        request=request,
    )


@router.get("/users/{user_id}/wallet", status_code=status.HTTP_200_OK)
async def get_user_wallet_profile(
    user_id: UUID,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_wallet_profile(user_id, request=request)


@router.post("/users/{user_id}/kyc/{kyc_id}/review", status_code=status.HTTP_200_OK)
async def review_kyc(
    user_id: UUID,
    kyc_id: UUID,
    payload: KYCApprovalSchema,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.review_kyc(user_id, kyc_id, payload, request=request)


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
        service_type=service_type,
        source_class=source_class,
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
        service_type=service_type,
        source_class=source_class,
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


@router.get("/users/{user_id}/wallets/{wallet_id}/ledger", status_code=status.HTTP_200_OK)
async def get_user_ledger(
    user_id: UUID,
    wallet_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    status_filter: str | None = Query(default=None, alias="status", max_length=50),
    entry_type: str | None = Query(default=None, max_length=50),
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_ledger(
        user_id=user_id,
        wallet_id=wallet_id,
        page=page,
        page_size=page_size,
        currency=currency,
        status_filter=status_filter,
        entry_type=entry_type,
        request=request,
    )


@router.get(
    "/users/{user_id}/wallets/{wallet_id}/ledger/reference",
    status_code=status.HTTP_200_OK,
)
async def get_user_ledger_by_reference(
    user_id: UUID,
    wallet_id: UUID,
    request: Request,
    transaction_reference: str = Query(..., min_length=1, max_length=100),
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_ledger_by_reference(
        user_id=user_id,
        wallet_id=wallet_id,
        transaction_reference=transaction_reference,
        request=request,
    )


@router.get("/wallet-adjustments", status_code=status.HTTP_200_OK)
async def list_admin_wallet_adjustments(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_admin_adjustments(
        page=page,
        page_size=page_size,
        search=search,
        start_date=start_date,
        end_date=end_date,
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
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user_id: UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status", max_length=50),
    category: str | None = Query(default=None, max_length=100),
    transaction_type: str | None = Query(default=None, max_length=100),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    search: str | None = Query(default=None, max_length=255),
    provider: str | None = Query(default=None, max_length=100),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    order_by: Literal[
        "created_at",
        "updated_at",
        "status",
        "amount",
        "category",
        "transaction_type",
        "currency",
        "reference",
    ] = "created_at",
    descending: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_transactions(
        page=page,
        page_size=page_size,
        user_id=user_id,
        status=status_filter,
        category=category,
        transaction_type=transaction_type,
        currency=currency,
        search=search,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        order_by=order_by,
        descending=descending,
        request=request,
    )


@router.get("/payments", status_code=status.HTTP_200_OK)
async def list_payments(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    payment_category: str | None = Query(default=None, max_length=50),
    status_filter: str | None = Query(default=None, alias="status", max_length=50),
    method: str | None = Query(default=None, max_length=100),
    search: str | None = Query(default=None, max_length=255),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    descending: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_payments(
        page=page,
        page_size=page_size,
        payment_category=payment_category,
        status=status_filter,
        method=method,
        search=search,
        start_date=start_date,
        end_date=end_date,
        descending=descending,
        request=request,
    )


@router.get("/payments/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_payment_details(
    transaction_id: UUID,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_payment_details(transaction_id, request=request)


@router.get("/users/{user_id}/transactions", status_code=status.HTTP_200_OK)
async def list_user_transactions(
    user_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status", max_length=50),
    category: str | None = Query(default=None, max_length=100),
    transaction_type: str | None = Query(default=None, max_length=100),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    search: str | None = Query(default=None, max_length=255),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    order_by: Literal[
        "created_at",
        "updated_at",
        "status",
        "amount",
        "category",
        "transaction_type",
        "currency",
        "reference",
    ] = "created_at",
    descending: bool = True,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_user_transactions(
        user_id=user_id,
        page=page,
        page_size=page_size,
        status=status_filter,
        category=category,
        transaction_type=transaction_type,
        currency=currency,
        search=search,
        start_date=start_date,
        end_date=end_date,
        order_by=order_by,
        descending=descending,
        request=request,
    )


@router.get("/transactions/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_transaction_details(
    transaction_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_transaction_details(UUID(transaction_id), request=request)


@router.get("/users/{user_id}/transactions/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_user_transaction_details(
    user_id: UUID,
    transaction_id: UUID,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_transaction_details(user_id, transaction_id, request=request)


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


@router.get("/finance", status_code=status.HTTP_200_OK)
async def get_finance_report(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    category: str | None = Query(default=None, max_length=100),
    status_filter: str | None = Query(default=None, alias="status", max_length=50),
    provider: str | None = Query(default=None, max_length=100),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    service_type: str | None = Query(default=None, max_length=50),
    source_class: str | None = Query(default=None, max_length=40),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_finance_report(
        page=page,
        page_size=page_size,
        search=search,
        category=category,
        status=status_filter,
        provider=provider,
        currency=currency,
        service_type=service_type,
        source_class=source_class,
        start_date=start_date,
        end_date=end_date,
        request=request,
    )


@router.get("/finance/transactions/{transaction_id}", status_code=status.HTTP_200_OK)
async def get_finance_transaction(
    transaction_id: UUID,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_finance_transaction(transaction_id, request=request)


@router.get("/provider-costs/providers", status_code=status.HTTP_200_OK)
async def list_provider_cost_providers(
    request: Request,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    return success_response(await service.list_providers(), "Provider list retrieved.")


@router.get("/provider-costs/configurations", status_code=status.HTTP_200_OK)
async def list_provider_cost_configurations(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    provider_id: UUID | None = None,
    service_type: str | None = Query(default=None, max_length=50),
    network_identifier: str | None = Query(default=None, max_length=120),
    product_identifier: str | None = Query(default=None, max_length=180),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    is_active: bool | None = None,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    result = await service.list_configurations(
        page=page,
        page_size=page_size,
        provider_id=provider_id,
        service_type=service_type.lower() if service_type else None,
        network_identifier=network_identifier,
        product_identifier=product_identifier,
        currency=currency,
        is_active=is_active,
    )
    return success_response(result, "Provider acquisition-cost configurations retrieved.")


@router.post("/provider-costs/configurations", status_code=status.HTTP_201_CREATED)
async def create_provider_cost_configuration(
    payload: ProviderAcquisitionCostCreate,
    request: Request,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    admin_id, _ = _provider_cost_admin_identity(request, write=True)
    try:
        result = await service.create_configuration(
            admin_id=admin_id,
            payload=payload.model_dump(mode="python"),
        )
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Provider acquisition-cost configuration created.", status.HTTP_201_CREATED)


@router.get("/provider-costs/configurations/{config_id}", status_code=status.HTTP_200_OK)
async def get_provider_cost_configuration(
    config_id: UUID,
    request: Request,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    try:
        result = await service.get_configuration(config_id)
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Provider acquisition-cost configuration retrieved.")


@router.put("/provider-costs/configurations/{config_id}", status_code=status.HTTP_200_OK)
async def update_provider_cost_configuration(
    config_id: UUID,
    payload: ProviderAcquisitionCostUpdate,
    request: Request,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    admin_id, _ = _provider_cost_admin_identity(request, write=True)
    try:
        result = await service.update_configuration(
            config_id,
            admin_id=admin_id,
            payload=payload.model_dump(mode="python", exclude_unset=True),
        )
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Provider acquisition-cost configuration version created.")


@router.post("/provider-costs/configurations/{config_id}/deactivate", status_code=status.HTTP_200_OK)
async def deactivate_provider_cost_configuration(
    config_id: UUID,
    request: Request,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    admin_id, _ = _provider_cost_admin_identity(request, write=True)
    try:
        result = await service.deactivate_configuration(config_id, admin_id=admin_id)
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Provider acquisition-cost configuration deactivated.")


@router.get("/provider-costs/configurations/{config_id}/history", status_code=status.HTTP_200_OK)
async def get_provider_cost_configuration_history(
    config_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    try:
        result = await service.list_history(config_id, page=page, page_size=page_size)
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Provider acquisition-cost history retrieved.")


@router.get("/provider-costs/configurations/{config_id}/transactions", status_code=status.HTTP_200_OK)
async def get_provider_cost_configuration_transactions(
    config_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    try:
        result = await service.transactions_for_configuration(
            config_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        _provider_cost_error(exc)
    return success_response(result, "Transactions using this configuration retrieved.")


@router.get("/provider-costs/transactions/awaiting", status_code=status.HTTP_200_OK)
async def list_transactions_awaiting_provider_cost(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    provider_id: UUID | None = None,
    service_type: str | None = Query(default=None, max_length=50),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    result = await service.list_awaiting(
        page=page,
        page_size=page_size,
        provider_id=provider_id,
        service_type=service_type.lower() if service_type else None,
        currency=currency,
    )
    return success_response(result, "Transactions awaiting cost verification retrieved.")


@router.get("/provider-costs/transactions", status_code=status.HTTP_200_OK)
async def list_provider_cost_snapshots(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    provider_id: UUID | None = None,
    service_type: str | None = Query(default=None, max_length=50),
    currency: str | None = Query(default=None, min_length=3, max_length=10),
    source_class: str | None = Query(default=None, max_length=40),
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    service: ProviderAcquisitionCostAdministrationService = Depends(get_provider_cost_administration_service),
) -> dict[str, Any]:
    _provider_cost_admin_identity(request)
    rows, total = await service.repository.list_snapshots(
        page=page,
        page_size=page_size,
        provider_id=provider_id,
        service_type=service_type.lower() if service_type else None,
        currency=currency,
        source_class=source_class.upper() if source_class else None,
        start_date=start_date,
        end_date=end_date,
    )
    result = {
        "items": [service._snapshot_data(snapshot, transaction) for snapshot, transaction in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }
    return success_response(result, "Provider cost snapshots retrieved.")


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
    user_id: UUID,
    request: Request,
    reason: str | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_user_session(
        session_id=session_id,
        user_id=user_id,
        reason=reason,
        request=request,
    )


@router.post("/users/{user_id}/sessions/{session_id}/revoke", status_code=status.HTTP_200_OK)
async def revoke_user_session_for_user(
    user_id: UUID,
    session_id: str,
    request: Request,
    reason: str | None = None,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.revoke_user_session_for_user(
        user_id,
        session_id,
        reason=reason,
        request=request,
    )


@router.get("/users/{user_id}/sessions", status_code=status.HTTP_200_OK)
async def list_user_sessions(
    user_id: str,
    request: Request,
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.list_user_sessions(UUID(user_id), request=request)


@router.get("/users/{user_id}/sessions/history", status_code=status.HTTP_200_OK)
async def get_user_session_history(
    user_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    controller: AdminController = Depends(get_admin_controller),
) -> dict[str, Any]:
    return await controller.get_user_session_history(
        user_id,
        page=page,
        page_size=page_size,
        request=request,
    )


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
