from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.provider import Provider
from app.services.giftcard.trading import GiftCardTradingService
from app.services.provider.failover import ProviderFailoverService
from app.services.provider.selector import ProviderSelector
from app.utils.exceptions import ProviderException


def make_provider(*, code: str, priority: int, active: bool = True, status: str = "active", category: str = "Gift Cards", metadata: str | None = None) -> Provider:
    return Provider(
        id=uuid4(),
        code=code,
        name=f"{code} gift cards",
        category=category,
        status=status,
        is_active=active,
        environment="production",
        priority=priority,
        metadata_payload=metadata,
    )


class ProviderRepositoryFake:
    def __init__(self, providers):
        self.providers = providers

    async def get_active_providers(self, *, category=None, service_type=None):
        return [provider for provider in self.providers if provider.is_active and provider.category == category]


class HealthFake:
    def __init__(self, healthy=None):
        self.healthy = healthy or {}
        self.successes = []
        self.failures = []

    async def is_provider_healthy(self, *, provider_id):
        return self.healthy.get(provider_id, True)

    async def record_success(self, **kwargs):
        self.successes.append(kwargs)

    async def record_failure(self, **kwargs):
        self.failures.append(kwargs)


async def execute_failover(providers, operation, *, healthy=None, max_retries=1, timeout=0.05):
    selector = ProviderSelector(provider_repository=ProviderRepositoryFake(providers))
    health = HealthFake(healthy=healthy)
    failover = ProviderFailoverService(
        selector=selector,
        health_service=health,
        max_retries=max_retries,
        provider_timeout_seconds=timeout,
        backoff_base_seconds=0,
        jitter_factor=0,
    )
    return await failover.execute_with_failover(
        operation=operation,
        category="Gift Cards",
        service_type="giftcard",
        use_cache=False,
    ), health


@pytest.mark.asyncio
async def test_priority_is_descending_and_provider_code_is_not_service_type():
    providers = [
        make_provider(code="sogo", priority=20),
        make_provider(code="prestmit", priority=30),
        make_provider(code="cardtonic", priority=40),
    ]
    selected = await ProviderSelector(
        provider_repository=ProviderRepositoryFake(providers)
    ).select_provider(category="Gift Cards", service_type="giftcard", use_cache=False)

    assert selected.code == "cardtonic"
    assert selected.code != "giftcard"


@pytest.mark.asyncio
async def test_inactive_unhealthy_and_wrong_category_providers_are_not_selected():
    providers = [
        make_provider(code="inactive", priority=100, active=False),
        make_provider(code="unhealthy", priority=90, status="unhealthy"),
        make_provider(code="airtime-provider", priority=80, category="Airtime"),
        make_provider(code="giftcard-provider", priority=10),
    ]
    selected = await ProviderSelector(
        provider_repository=ProviderRepositoryFake(providers)
    ).select_provider(category="Gift Cards", service_type="giftcard", use_cache=False)

    assert selected.code == "giftcard-provider"


@pytest.mark.asyncio
async def test_primary_success_does_not_execute_secondary():
    providers = [make_provider(code="primary", priority=20), make_provider(code="secondary", priority=10)]
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        return {"status": "success", "provider_reference": "primary-ref"}

    result, health = await execute_failover(providers, operation, max_retries=1)

    assert result["provider_reference"] == "primary-ref"
    assert attempts == ["primary"]
    assert len(health.failures) == 0


@pytest.mark.asyncio
async def test_primary_retryable_failure_fails_over_to_secondary_without_shared_result():
    providers = [make_provider(code="primary", priority=20), make_provider(code="secondary", priority=10)]
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        if provider.code == "primary":
            raise ConnectionError("primary unavailable")
        return {"status": "success", "provider_reference": "secondary-ref"}

    result, health = await execute_failover(providers, operation, max_retries=1)

    assert attempts == ["primary", "secondary"]
    assert result == {"status": "success", "provider_reference": "secondary-ref"}
    assert len(health.failures) == 1


@pytest.mark.asyncio
async def test_all_provider_failures_never_return_success():
    providers = [make_provider(code="primary", priority=20), make_provider(code="secondary", priority=10)]
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        raise ConnectionError(f"{provider.code} unavailable")

    with pytest.raises(ProviderException, match="unavailable"):
        await execute_failover(providers, operation, max_retries=1)

    assert attempts == ["primary", "secondary"]


@pytest.mark.asyncio
async def test_timeout_is_retryable_and_secondary_can_succeed():
    providers = [make_provider(code="slow", priority=20), make_provider(code="ready", priority=10)]
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        if provider.code == "slow":
            await asyncio.sleep(0.2)
        return {"status": "success", "provider_reference": "ready-ref"}

    result, health = await execute_failover(providers, operation, max_retries=1, timeout=0.01)

    assert attempts == ["slow", "ready"]
    assert result["provider_reference"] == "ready-ref"
    assert len(health.failures) == 1


@pytest.mark.asyncio
async def test_explicit_empty_retry_policy_stops_after_first_timeout():
    providers = [make_provider(code="primary", priority=20), make_provider(code="secondary", priority=10)]
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        raise TimeoutError("provider outcome is unknown")

    with pytest.raises(ProviderException, match="unknown"):
        await execute_failover_with_policy(providers, operation, retryable_errors=())

    assert attempts == ["primary"]


async def execute_failover_with_policy(providers, operation, *, retryable_errors):
    selector = ProviderSelector(provider_repository=ProviderRepositoryFake(providers))
    health = HealthFake()
    failover = ProviderFailoverService(
        selector=selector,
        health_service=health,
        max_retries=1,
        provider_timeout_seconds=0.05,
        backoff_base_seconds=0,
        jitter_factor=0,
    )
    return await failover.execute_with_failover(
        operation=operation,
        category="Gift Cards",
        service_type="giftcard",
        use_cache=False,
        retryable_errors=retryable_errors,
    )


@pytest.mark.asyncio
async def test_unhealthy_primary_is_skipped_and_secondary_executes():
    primary = make_provider(code="primary", priority=20)
    secondary = make_provider(code="secondary", priority=10)
    attempts = []

    async def operation(provider):
        attempts.append(provider.code)
        return {"status": "success", "provider_reference": "secondary-ref"}

    result, _ = await execute_failover(
        [primary, secondary],
        operation,
        healthy={primary.id: False, secondary.id: True},
        max_retries=1,
    )

    assert attempts == ["secondary"]
    assert result["provider_reference"] == "secondary-ref"


def test_giftcard_response_normalization_preserves_provider_reference():
    service = GiftCardTradingService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        transaction_repository=SimpleNamespace(),
        user_repository=SimpleNamespace(),
        wallet_repository=SimpleNamespace(),
    )
    provider = make_provider(code="sogo", priority=10)

    normalized = service._normalize_provider_response(
        {"status": "success", "provider_reference": "SOGO-TEST-123", "provider_transaction_id": "tx-1"},
        provider,
    )

    assert normalized["provider_reference"] == "SOGO-TEST-123"
    assert normalized["provider_transaction_id"] == "tx-1"
    assert normalized["provider"] == provider.name


def test_giftcard_request_idempotency_requires_explicit_reference():
    service = GiftCardTradingService(
        wallet_service=SimpleNamespace(),
        provider_service=SimpleNamespace(),
        transaction_repository=SimpleNamespace(),
        user_repository=SimpleNamespace(),
        wallet_repository=SimpleNamespace(),
    )

    generated_one = service.generate_reference("buy")
    generated_two = service.generate_reference("buy")

    assert generated_one != generated_two
    assert "giftcard-buy-" in generated_one
    assert "giftcard-buy-" in generated_two
