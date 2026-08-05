from __future__ import annotations

import logging
from typing import Any

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

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.dashboard_service = DashboardService(logger=self.logger)
        self.user_service = UserAdministrationService(logger=self.logger)
        self.kyc_service = KYCService(logger=self.logger)
        self.wallet_service = WalletAdministrationService(logger=self.logger)
        self.transaction_service = TransactionAdministrationService(logger=self.logger)
        self.payment_service = PaymentAdministrationService(logger=self.logger)
        self.finance_service = FinanceService(logger=self.logger)
        self.fraud_service = FraudService(logger=self.logger)
        self.provider_service = ProviderAdministrationService(logger=self.logger)
        self.settings_service = SettingsService(logger=self.logger)
        self.audit_service = AuditService(logger=self.logger)
        self.api_key_service = ApiKeyService(logger=self.logger)
        self.notification_service = NotificationAdministrationService(logger=self.logger)
        self.monitoring_service = MonitoringService(logger=self.logger)
        self.report_service = ReportService(logger=self.logger)
        self.staff_service = StaffAdministrationService(logger=self.logger)
        self.support_service = SupportAdministrationService(logger=self.logger)
        self.security_service = SecurityAdministrationService(logger=self.logger)
        self.session_service = SessionAdministrationService(logger=self.logger)
        self.product_service = ProductAdministrationService(logger=self.logger)
        self.marketing_service = MarketingService(logger=self.logger)
        self.merchant_service = MerchantService(logger=self.logger)

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

    async def list_transactions(self, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.list_transactions(**payload)

    async def reverse_transaction(self, *, transaction_id: str, **payload: Any) -> dict[str, Any]:
        return await self.transaction_service.reverse_transaction(transaction_id=transaction_id, **payload)

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
