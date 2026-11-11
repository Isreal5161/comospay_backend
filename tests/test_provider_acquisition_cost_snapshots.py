from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models import Admin, Provider, ProviderAcquisitionCost, ProviderCostSnapshot, Transaction, User, Wallet
from app.repositories.provider_acquisition_cost_repository import ProviderAcquisitionCostRepository
from app.services.provider_acquisition_cost_service import (
    ProviderAcquisitionCostAdministrationService,
    ProviderCostSnapshotService,
)


class FakeProviderCostRepository:
    def __init__(self, *, configuration=None, existing=None) -> None:
        self.provider = SimpleNamespace(
            id=uuid4(),
            name="AidaPay",
            code="aidapay",
            category="airtime",
        )
        self.configuration = configuration
        self.existing = existing
        self.created = []
        self.lookup = None

    @staticmethod
    def service_details(transaction):
        return ProviderAcquisitionCostRepository.service_details(transaction)

    async def get_transaction_snapshot(self, transaction_id):
        return self.existing

    async def list_providers(self):
        return [self.provider]

    async def find_matching_configuration(self, **kwargs):
        self.lookup = kwargs
        return self.configuration

    async def create_snapshot(self, snapshot):
        self.created.append(snapshot)
        return snapshot


def transaction(*, status: str = "succeeded", provider_name: str | None = "aidapay"):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        transaction_type="airtime_purchase",
        category="airtime",
        metadata_payload=json.dumps({"network": "MTN"}),
        provider_name=provider_name,
        provider_reference="provider-ref-001",
        provider_transaction_id="provider-tx-001",
        currency="NGN",
        amount=Decimal("100.00"),
    )


@pytest.mark.parametrize(
    ("service_variant", "expected_service_type"),
    [("awoof", "awoof_data"), ("data", "data")],
)
def test_data_service_details_classify_only_explicit_awoof_variant(
    service_variant: str,
    expected_service_type: str,
) -> None:
    current = SimpleNamespace(
        transaction_type="data_purchase",
        metadata_payload=json.dumps(
            {
                "service_variant": service_variant,
                "network": "MTN",
                "bundle_code": "MTN-1GB",
            }
        ),
    )

    assert ProviderAcquisitionCostRepository.service_details(current) == (
        expected_service_type,
        "MTN",
        "MTN-1GB",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cost_method", "cost_value", "selling_amount", "expected_cost", "expected_margin"),
    [
        ("fixed_amount", "70.00", "100.00", "70.00", "30.00"),
        ("fixed_amount", "120.00", "100.00", "120.00", "-20.00"),
        ("percentage_of_selling_amount", "10.00", "100.00", "10.00", "90.00"),
    ],
)
async def test_successful_transaction_snapshots_admin_configured_cost(
    cost_method: str,
    cost_value: str,
    selling_amount: str,
    expected_cost: str,
    expected_margin: str,
) -> None:
    repository = FakeProviderCostRepository(
        configuration=SimpleNamespace(
            id=uuid4(),
            cost_method=cost_method,
            cost_value=Decimal(cost_value),
        )
    )
    current = transaction()
    current.amount = Decimal(selling_amount)

    snapshot = await ProviderCostSnapshotService(repository).capture_successful_transaction(current)

    assert snapshot is repository.created[0]
    assert snapshot.provider_id == repository.provider.id
    assert snapshot.provider_identifier == "aidapay"
    assert snapshot.provider_reference == "provider-ref-001"
    assert snapshot.service_type == "airtime"
    assert snapshot.network_identifier == "MTN"
    assert snapshot.selling_amount == Decimal(selling_amount)
    assert snapshot.cost_amount == Decimal(expected_cost)
    assert snapshot.gross_margin == Decimal(expected_margin)
    assert snapshot.currency == "NGN"
    assert snapshot.source_class == "ADMIN_CONFIGURED"
    assert snapshot.configuration_id == repository.configuration.id


@pytest.mark.asyncio
async def test_successful_transaction_without_matching_cost_is_unavailable_not_estimated() -> None:
    repository = FakeProviderCostRepository()

    snapshot = await ProviderCostSnapshotService(repository).capture_successful_transaction(transaction())

    assert snapshot is repository.created[0]
    assert snapshot.source_class == "UNAVAILABLE"
    assert snapshot.cost_amount is None
    assert snapshot.gross_margin is None
    assert snapshot.configuration_id is None
    assert snapshot.reason


@pytest.mark.asyncio
async def test_pending_transaction_does_not_create_cost_snapshot() -> None:
    repository = FakeProviderCostRepository(
        configuration=SimpleNamespace(
            id=uuid4(),
            cost_method="fixed_amount",
            cost_value=Decimal("70.00"),
        )
    )

    snapshot = await ProviderCostSnapshotService(repository).capture_successful_transaction(
        transaction(status="pending")
    )

    assert snapshot is None
    assert repository.created == []
    assert repository.lookup is None


@pytest.mark.asyncio
async def test_missing_final_provider_is_recorded_as_unavailable() -> None:
    repository = FakeProviderCostRepository(
        configuration=SimpleNamespace(
            id=uuid4(),
            cost_method="fixed_amount",
            cost_value=Decimal("70.00"),
        )
    )

    snapshot = await ProviderCostSnapshotService(repository).capture_successful_transaction(
        transaction(provider_name=None)
    )

    assert snapshot is repository.created[0]
    assert snapshot.provider_id is None
    assert snapshot.source_class == "UNAVAILABLE"
    assert snapshot.cost_amount is None
    assert repository.lookup is None


@pytest.mark.asyncio
async def test_reprocessing_returns_existing_snapshot_without_repricing() -> None:
    original = SimpleNamespace(
        cost_amount=Decimal("70.00"),
        gross_margin=Decimal("30.00"),
        source_class="ADMIN_CONFIGURED",
    )
    repository = FakeProviderCostRepository(
        existing=original,
        configuration=SimpleNamespace(
            id=uuid4(),
            cost_method="fixed_amount",
            cost_value=Decimal("20.00"),
        ),
    )

    snapshot = await ProviderCostSnapshotService(repository).capture_successful_transaction(transaction())

    assert snapshot is original
    assert snapshot.cost_amount == Decimal("70.00")
    assert repository.created == []
    assert repository.lookup is None


@pytest_asyncio.fixture
async def cost_database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_snapshot_is_persisted_once_and_remains_unchanged_after_rule_edit(cost_database) -> None:
    async with cost_database() as session:
        admin = Admin(email="cost-admin@example.test", role="admin", status="active")
        user = User(email="cost-user@example.test", status="active")
        session.add_all([admin, user])
        await session.flush()
        wallet = Wallet(user_id=user.id, currency="NGN")
        provider = Provider(
            name="AidaPay",
            code="aidapay",
            category="airtime",
            status="active",
            is_active=True,
        )
        session.add_all([wallet, provider])
        await session.flush()
        transaction_record = Transaction(
            reference="COST-SNAPSHOT-001",
            user_id=user.id,
            wallet_id=wallet.id,
            transaction_type="airtime_purchase",
            category="airtime",
            amount=Decimal("100.00"),
            currency="NGN",
            charges=Decimal("7.00"),
            total_amount=Decimal("107.00"),
            status="completed",
            provider_name="AidaPay",
            provider_reference="provider-ref-001",
            provider_transaction_id="provider-tx-001",
            metadata_payload=json.dumps({"network": "MTN"}),
        )
        session.add(transaction_record)
        configuration = ProviderAcquisitionCost(
            provider_id=provider.id,
            service_type="airtime",
            network_identifier="MTN",
            product_identifier=None,
            currency="NGN",
            cost_method="fixed_amount",
            cost_value=Decimal("70.00"),
            is_active=True,
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            effective_until=None,
            evidence_note="Test admin configuration; not provider debit evidence.",
            created_by=admin.id,
            updated_by=admin.id,
        )
        session.add(configuration)
        await session.flush()

        service = ProviderCostSnapshotService(ProviderAcquisitionCostRepository(session))
        first = await service.capture_successful_transaction(transaction_record)
        configuration.cost_value = Decimal("90.00")
        await session.flush()
        second = await service.capture_successful_transaction(transaction_record)

        persisted = await session.scalar(
            select(func.count(ProviderCostSnapshot.id)).where(
                ProviderCostSnapshot.transaction_id == transaction_record.id
            )
        )
        await session.refresh(wallet)
        await session.refresh(transaction_record)

        assert first is not None
        assert first.source_class == "ADMIN_CONFIGURED"
        assert first.cost_amount == Decimal("70.00")
        assert second.id == first.id
        assert second.cost_amount == Decimal("70.00")
        assert persisted == 1
        assert transaction_record.amount == Decimal("100.00")
        assert transaction_record.charges == Decimal("7.00")
        assert transaction_record.total_amount == Decimal("107.00")
        assert wallet.available_balance == Decimal("0.00")
        assert wallet.ledger_balance == Decimal("0.00")

        awaiting = await ProviderAcquisitionCostAdministrationService(
            session,
            ProviderAcquisitionCostRepository(session),
        ).list_awaiting()
        assert awaiting["total"] == 1
        assert awaiting["items"][0]["source_class"] == "ADMIN_CONFIGURED"
        assert "not verified" in awaiting["items"][0]["reason"]
