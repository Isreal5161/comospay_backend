import pytest

from app.routes.provider_routes import get_provider_service


@pytest.mark.asyncio
async def test_get_provider_service_builds_provider_graph() -> None:
    service = await get_provider_service(session=object())

    assert service is not None
    assert service.selector is not None
    assert service.health_service is not None
    assert service.failover_service is not None
    assert service.provider_repository is not None
