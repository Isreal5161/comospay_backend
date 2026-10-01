from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.config.settings import settings
from app.schemas.admin_schema import AdminResponse
from app.utils.exceptions import AuthenticationException, DatabaseException
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


class AdminService:
    """Thin administration façade that delegates to modular admin services."""

    def __init__(
        self,
        *,
        dashboard_service: DashboardService,
        user_service: UserAdministrationService,
        kyc_service: KYCService,
        wallet_service: WalletAdministrationService,
        transaction_service: TransactionAdministrationService,
        payment_service: PaymentAdministrationService,
        finance_service: FinanceService,
        fraud_service: FraudService,
        provider_service: ProviderAdministrationService,
        settings_service: SettingsService,
        audit_service: AuditService,
        api_key_service: ApiKeyService,
        notification_service: NotificationAdministrationService,
        monitoring_service: MonitoringService,
        report_service: ReportService,
        staff_service: StaffAdministrationService,
        support_service: SupportAdministrationService,
        security_service: SecurityAdministrationService,
        session_service: SessionAdministrationService,
        product_service: ProductAdministrationService,
        marketing_service: MarketingService,
        merchant_service: MerchantService,
        admin_repository: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.admin_repository = admin_repository
        self.dashboard_service = dashboard_service
        self.user_service = user_service
        self.kyc_service = kyc_service
        self.wallet_service = wallet_service
        self.transaction_service = transaction_service
        self.payment_service = payment_service
        self.finance_service = finance_service
        self.fraud_service = fraud_service
        self.provider_service = provider_service
        self.settings_service = settings_service
        self.audit_service = audit_service
        self.api_key_service = api_key_service
        self.notification_service = notification_service
        self.monitoring_service = monitoring_service
        self.report_service = report_service
        self.staff_service = staff_service
        self.support_service = support_service
        self.security_service = security_service
        self.session_service = session_service
        self.product_service = product_service
        self.marketing_service = marketing_service
        self.merchant_service = merchant_service

    async def get_admin_profile(self, *, admin_id: UUID, token_role: str) -> dict[str, Any]:
        """Return the safe profile for the authenticated, currently active Admin."""
        if self.admin_repository is None:
            raise DatabaseException("Admin profile service is unavailable.")
        admin = await self.admin_repository.get_by_id(admin_id)
        if admin is None or not bool(getattr(admin, "is_active", False)):
            raise AuthenticationException("Admin account is unavailable.")
        if str(getattr(admin, "status", "") or "").strip().lower() != "active":
            raise AuthenticationException("Admin account is unavailable.")

        normalize = lambda role: str(role).strip().lower().replace(" ", "_")
        configured_roles = getattr(settings, "admin_allowed_roles", None)
        if isinstance(configured_roles, str):
            configured_roles = [item.strip() for item in configured_roles.split(",") if item.strip()]
        allowed_roles = configured_roles or ["super_admin", "admin"]
        current_role = normalize(getattr(admin, "role", ""))
        if current_role != normalize(token_role) or current_role not in {normalize(role) for role in allowed_roles}:
            raise AuthenticationException("Admin account is unavailable.")

        return AdminResponse.model_validate(admin).model_dump(mode="json")

    async def get_dashboard_stats(self, **payload: Any) -> dict[str, Any]:
        return await self.dashboard_service.get_stats(**payload)

    async def list_users(self, **payload: Any) -> dict[str, Any]:
        return await self.user_service.list_users(**payload)

    async def get_user(self, *, user_id, **payload: Any) -> dict[str, Any]:
        return await self.user_service.get_user(user_id=user_id, **payload)

    async def manage_user(self, *, user_id, action: str, **payload: Any) -> dict[str, Any]:
        return await self.user_service.manage_user(user_id=user_id, action=action, **payload)

    async def review_kyc(self, *, user_id, action: str, **payload: Any) -> dict[str, Any]:
        return await self.kyc_service.review_kyc(user_id=user_id, action=action, **payload)

    async def adjust_wallet(self, *, user_id, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.adjust_wallet(user_id=user_id, amount=amount, reason=reason, **payload)

    async def list_wallets(self, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.list_wallets(**payload)

    async def get_wallet_details(self, *, wallet_id, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.get_wallet_details(wallet_id=wallet_id, **payload)

    async def get_user_wallet(self, *, user_id, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.get_user_wallet(user_id=user_id, **payload)

    async def search_wallets(self, *, query: str, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.search_wallets(query=query, **payload)

    async def wallet_history(self, *, wallet_id, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.wallet_history(wallet_id=wallet_id, **payload)

    async def wallet_analytics(self, *, wallet_id, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.wallet_analytics(wallet_id=wallet_id, **payload)

    async def freeze_wallet(self, *, wallet_id, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.freeze_wallet(wallet_id=wallet_id, reason=reason, **payload)

    async def unfreeze_wallet(self, *, wallet_id, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.unfreeze_wallet(wallet_id=wallet_id, **payload)

    async def lock_wallet(self, *, wallet_id, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.lock_wallet(wallet_id=wallet_id, amount=amount, reason=reason, **payload)

    async def unlock_wallet(self, *, wallet_id, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.unlock_wallet(wallet_id=wallet_id, amount=amount, reason=reason, **payload)

    async def reconcile_wallet(self, *, wallet_id, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        return await self.wallet_service.reconcile_wallet(wallet_id=wallet_id, dry_run=dry_run, **payload)

    async def list_transactions(self, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.list_transactions(**payload)

    async def get_transaction_details(self, *, transaction_id, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.get_transaction_details(transaction_id=transaction_id, **payload)

    async def reverse_transaction(self, *, transaction_id: str, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.reverse_transaction(transaction_id=transaction_id, **payload)

    async def retry_failed_transaction(self, *, transaction_id, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.retry_failed_transaction(transaction_id=transaction_id, **payload)

    async def resolve_transaction(self, *, transaction_id, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.resolve_transaction(transaction_id=transaction_id, **payload)

    async def transaction_timeline(self, *, transaction_id, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.transaction_timeline(transaction_id=transaction_id, **payload)

    async def list_providers(self, **payload: Any) -> dict[str, Any]:
        return await self.provider_service.list_providers(**payload)

    async def configure_provider(self, *, provider_id: str, **payload: Any) -> dict[str, Any]:
        return await self.provider_service.configure_provider(provider_id=provider_id, **payload)

    async def get_system_settings(self, **payload: Any) -> dict[str, Any]:
        return await self.settings_service.get_settings(**payload)

    async def update_system_settings(self, **payload: Any) -> dict[str, Any]:
        return await self.settings_service.update_settings(**payload)

    async def list_audit_logs(self, **payload: Any) -> dict[str, Any]:
        return await self.audit_service.list_audit_logs(**payload)

    async def create_api_key(self, **payload: Any) -> dict[str, Any]:
        return await self.api_key_service.create_api_key(**payload)

    async def update_api_key(self, *, key_id: str, **payload: Any) -> dict[str, Any]:
        return await self.api_key_service.update_api_key(key_id=key_id, **payload)

    async def revoke_api_key(self, *, key_id: str, **payload: Any) -> dict[str, Any]:
        return await self.api_key_service.revoke_api_key(key_id=key_id, **payload)

    async def broadcast_notification(self, **payload: Any) -> dict[str, Any]:
        return await self.notification_service.broadcast_notification(**payload)

    async def get_platform_statistics(self, **payload: Any) -> dict[str, Any]:
        return await self.finance_service.get_platform_statistics(**payload)

    async def get_service_monitoring(self, **payload: Any) -> dict[str, Any]:
        return await self.monitoring_service.get_service_monitoring(**payload)

    async def generate_report(self, **payload: Any) -> dict[str, Any]:
        return await self.report_service.generate_report(**payload)

    async def create_admin_account(self, **payload: Any) -> dict[str, Any]:
        return await self.staff_service.create_admin_account(**payload)

    async def update_admin_account(self, *, admin_id: str, **payload: Any) -> dict[str, Any]:
        return await self.staff_service.update_admin_account(admin_id=admin_id, **payload)

    async def revoke_user_session(self, *, session_id: str, **payload: Any) -> dict[str, Any]:
        return await self.session_service.revoke_session_by_id(session_id=session_id, **payload)

    async def list_user_sessions(self, *, user_id, **payload: Any) -> dict[str, Any]:
        return await self.session_service.get_user_sessions(user_id=user_id, **payload)

    async def revoke_all_user_sessions(self, *, user_id, **payload: Any) -> dict[str, Any]:
        return await self.session_service.revoke_all_user_sessions(user_id=user_id, **payload)

    async def get_active_session_count(self, *, user_id, **payload: Any) -> dict[str, Any]:
        return await self.session_service.get_active_session_count(user_id=user_id, **payload)
