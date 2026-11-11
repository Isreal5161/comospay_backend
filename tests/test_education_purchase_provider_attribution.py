from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.education.purchase import EducationPurchaseService


class FakeTransactionRepository:
    def __init__(self, transaction) -> None:
        self.transaction = transaction
        self.session = SimpleNamespace(begin=self._begin)

    def _begin(self):
        @asynccontextmanager
        async def context():
            yield

        return context()

    async def get_by_reference(self, reference):
        return self.transaction if reference == self.transaction.reference else None

    async def update_transaction(self, transaction, **values):
        for key, value in values.items():
            setattr(transaction, key, value)
        return transaction


class FakeProviderService:
    async def execute_education(self, *, operation, validate, normalize, payload):
        provider = SimpleNamespace(name="actual-provider")
        result = await operation(provider)
        validate(result)
        return normalize(result, provider)


@pytest.mark.asyncio
async def test_education_purchase_persists_provider_selected_by_execution():
    now = datetime.now(timezone.utc)
    transaction = SimpleNamespace(
        reference="education-001",
        user_id=uuid4(),
        wallet_id=None,
        amount=Decimal("100.00"),
        currency="NGN",
        status="pending",
        provider_name="requested-provider",
        provider_reference=None,
        provider_transaction_id=None,
        external_reference=None,
        description="Education purchase",
        metadata_payload=json.dumps({
            "provider": "waec",
            "examination_type": "waec",
            "candidate_number": "1234567890",
            "quantity": 1,
            "examination_year": 2025,
        }),
        created_at=now,
        updated_at=now,
    )
    repository = FakeTransactionRepository(transaction)
    service = EducationPurchaseService(
        wallet_service=SimpleNamespace(),
        provider_service=FakeProviderService(),
        transaction_repository=repository,
        user_repository=SimpleNamespace(),
        wallet_repository=SimpleNamespace(),
    )

    result = await service.process_education_purchase(
        transaction=transaction,
        provider_name="requested-provider",
        provider_operation=lambda provider: _successful_provider_response(),
    )

    assert result["status"] == "succeeded"
    assert transaction.provider_name == "actual-provider"
    assert transaction.provider_reference == "education-ref-001"


async def _successful_provider_response():
    return {
        "status": "success",
        "provider": "provider-claimed-by-payload",
        "provider_reference": "education-ref-001",
        "provider_transaction_id": "education-tx-001",
    }
