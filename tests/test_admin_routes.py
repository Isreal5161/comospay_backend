import pytest

from app.routes import admin_routes


@pytest.mark.asyncio
async def test_get_admin_service_builds_admin_subservices():
    service = await admin_routes.get_admin_service(session=object())

    assert service is not None
    assert service.dashboard_service is not None
    assert service.user_service is not None
    assert service.kyc_service is not None
    assert service.wallet_service is not None
    assert service.transaction_service is not None
    assert service.provider_service is not None
    assert service.settings_service is not None
    assert service.audit_service is not None
    assert service.report_service is not None
    assert service.session_service is not None
