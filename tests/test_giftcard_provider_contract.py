from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.provider import Provider
from app.services.giftcard_service import GiftCardService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.selector import ProviderSelector
from app.services.provider_service import ProviderService
from app.utils.exceptions import ProviderException


class FakeGiftCardIntegration:
    async def submit_card(self, *, card_data):
        return {"status": "success", "provider_reference": card_data["reference"]}

    async def get_transaction_status(self, *, provider_reference=None):
        return {"status": "pending", "provider_reference": provider_reference}

    async def verify_card(self, *, card_data):
        return {"status": "valid", "card_data": card_data}

    async def get_exchange_rate(self, **kwargs):
        return {"status": "success", **kwargs}

    async def get_supported_cards(self):
        return []

    async def health_check(self):
        return {"status": "healthy"}


def make_provider(*, code="sogo", priority=0, is_active=True):
    return Provider(
        id=uuid4(),
        code=code,
        name=f"{code} gift cards",
        category="Gift Cards",
        status="active",
        is_active=is_active,
        environment="production",
        priority=priority,
    )


def make_service(*, builder=None):
    return GiftCardService(
        trading_service=SimpleNamespace(),
        valuation_service=SimpleNamespace(),
        pricing_service=SimpleNamespace(),
        settlement_service=SimpleNamespace(),
        reconciliation_service=SimpleNamespace(),
        provider_integration_builder=builder,
    )


@pytest.mark.asyncio
async def test_giftcard_service_builds_provider_operation_and_delegates_to_integration():
    integration = FakeGiftCardIntegration()
    service = make_service(builder=lambda provider: integration)
    provider = make_provider(code="sogo")
    operation = service.build_provider_operation(
        operation="buy",
        payload={"reference": "giftcard-buy-1", "brand": "Amazon"},
    )

    result = await operation(provider)

    assert result["status"] == "success"
    assert result["provider_reference"] == "giftcard-buy-1"

@pytest.mark.asyncio
async def test_provider_service_execute_giftcard_receives_operation():
    provider = make_provider(code="sogo")
    calls = []

    class FakeSelector:
        provider_repository = SimpleNamespace(get_provider_by_id=lambda provider_id: None)

        async def select_provider(self, **kwargs):
            return provider

    class FakeRepository:
        async def get_provider_by_id(self, provider_id):
            return provider

    class FakeHealth:
        async def record_success(self, **kwargs):
            pass

    class FakeFailover:
        async def execute_with_failover(self, *, operation, **kwargs):
            calls.append(kwargs)
            return await operation(provider)

    service = ProviderService(
        selector=FakeSelector(),
        health_service=FakeHealth(),
        failover_service=FakeFailover(),
        provider_repository=FakeRepository(),
    )
    operation = make_service(builder=lambda selected: FakeGiftCardIntegration()).build_provider_operation(
        operation="buy", payload={"reference": "ref-1"}
    )

    result = await service.execute_giftcard(operation=operation, payload={"reference": "ref-1"})

    assert result["status"] == "success"
    assert calls[0]["category"] == "Gift Cards"
    assert calls[0]["service_type"] == "giftcard"


@pytest.mark.asyncio
async def test_provider_selector_uses_category_without_matching_provider_code():
    providers = [make_provider(code="sogo", priority=10)]

    class Repository:
        async def get_active_providers(self, **kwargs):
            assert kwargs == {"category": "Gift Cards", "service_type": "giftcard"}
            return providers

    selected = await ProviderSelector(provider_repository=Repository()).select_provider(
        category="Gift Cards", service_type="giftcard", use_cache=False
    )

    assert selected.code == "sogo"


@pytest.mark.asyncio
async def test_provider_selector_excludes_inactive_and_respects_priority():
    providers = [
        make_provider(code="inactive", priority=100, is_active=False),
        make_provider(code="provider-b", priority=20),
        make_provider(code="provider-a", priority=10),
    ]

    class Repository:
        async def get_active_providers(self, **kwargs):
            return [provider for provider in providers if provider.is_active]

    selected = await ProviderSelector(provider_repository=Repository()).select_provider(
        category="Gift Cards", service_type="giftcard", use_cache=False
    )

    assert selected.code == "provider-b"


@pytest.mark.asyncio
async def test_provider_failover_executes_giftcard_operation():
    providers = [make_provider(code="provider-a", priority=20), make_provider(code="provider-b", priority=10)]
    attempts = []

    class Repository:
        async def get_active_providers(self, **kwargs):
            return providers

    class Health:
        async def is_provider_healthy(self, **kwargs):
            return True

        async def record_success(self, **kwargs):
            pass

        async def record_failure(self, **kwargs):
            pass

    selector = ProviderSelector(provider_repository=Repository())
    failover = ProviderFailoverService(
        selector=selector,
        health_service=Health(),
        max_retries=0,
    )

    async def operation(provider):
        attempts.append(provider.code)
        return {"status": "success"}

    result = await failover.execute_with_failover(
        operation=operation,
        category="Gift Cards",
        service_type="giftcard",
        use_cache=False,
    )

    assert result["status"] == "success"
    assert attempts == ["provider-a"]


@pytest.mark.asyncio
async def test_giftcard_provider_failure_propagates_without_external_call():
    service = make_service(builder=lambda provider: FakeGiftCardIntegration())
    provider = make_provider()

    class FailingIntegration(FakeGiftCardIntegration):
        async def submit_card(self, *, card_data):
            raise ProviderException("provider unavailable")

    service.provider_integration_builder = lambda selected: FailingIntegration()
    operation = service.build_provider_operation(operation="buy", payload={"reference": "ref-1"})

    with pytest.raises(ProviderException, match="provider unavailable"):
        await operation(provider)


@pytest.mark.asyncio
async def test_provider_repository_query_does_not_equate_service_type_to_provider_code():
    captured = {}

    class Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class Session:
        async def execute(self, statement):
            captured["statement"] = statement
            return Result()

    from app.repositories.provider_repository import ProviderRepository

    await ProviderRepository(session=Session()).get_active_providers(
        category="Gift Cards", service_type="giftcard"
    )

    sql = str(captured["statement"])
    assert "providers.category" in sql
    assert "providers.code =" not in sql
