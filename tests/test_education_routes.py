import pytest

from app.routes import education_routes


@pytest.mark.asyncio
async def test_get_education_service_builds_subservices_from_session():
    session = object()

    service = await education_routes.get_education_service(session=session)

    assert service is not None
    assert service.purchase_service is not None
    assert service.validation_service is not None
    assert service.pricing_service is not None
    assert service.result_service is not None
    assert service.reconciliation_service is not None
    assert service.purchase_service.wallet_service is not None
