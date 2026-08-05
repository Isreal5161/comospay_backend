import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.wallet.wallet_reconciliation import WalletReconciliationService


class FakeSession:
    def __init__(self, transactions=None):
        self._transactions = transactions or []

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self._transactions)))


class FakeTransactionRepository:
    def __init__(self, transactions):
        self.session = FakeSession(transactions)
        self._transactions = transactions

    async def get_by_id(self, transaction_id):
        return next((item for item in self._transactions if item.id == transaction_id), None)


class FakeWalletRepository:
    def __init__(self, wallet):
        self.session = FakeSession()
        self._wallet = wallet

    async def get_by_id(self, wallet_id):
        return self._wallet if self._wallet.id == wallet_id else None

    async def get_by_id_for_update(self, wallet_id):
        return self._wallet if self._wallet.id == wallet_id else None


def test_validate_wallet_consistency_and_rebuild_dry_run() -> None:
    wallet_id = uuid4()
    user_id = uuid4()
    wallet = SimpleNamespace(
        id=wallet_id,
        user_id=user_id,
        available_balance=Decimal("100.00"),
        ledger_balance=Decimal("100.00"),
        currency="NGN",
        status="active",
    )
    transaction = SimpleNamespace(
        id=uuid4(),
        reference="txn-001",
        wallet_id=wallet_id,
        user_id=user_id,
        transaction_type="wallet_funding",
        category="funding",
        amount=Decimal("100.00"),
        total_amount=Decimal("100.00"),
        status="completed",
        provider_name=None,
        provider_reference=None,
        created_at=datetime.now(timezone.utc),
    )

    service = WalletReconciliationService(
        wallet_repository=FakeWalletRepository(wallet),
        transaction_repository=FakeTransactionRepository([transaction]),
    )

    consistency = asyncio.run(service.validate_wallet_consistency(wallet_id=wallet_id))
    assert consistency["is_consistent"] is True

    rebuild = asyncio.run(service.rebuild_wallet_balance(wallet_id=wallet_id, dry_run=True))
    assert rebuild["dry_run"] is True
    assert rebuild["would_update"] is False
