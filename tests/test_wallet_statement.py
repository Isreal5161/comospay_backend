import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.services.wallet.statement import WalletStatementService


class DummyWalletRepository(SimpleNamespace):
    def __init__(self, wallet) -> None:
        super().__init__()
        self._wallet = wallet

    async def get_by_id(self, wallet_id):
        if wallet_id == self._wallet.id:
            return self._wallet
        return None


class DummyTransactionRepository(SimpleNamespace):
    def __init__(self, transactions) -> None:
        super().__init__()
        self._transactions = transactions

    async def get_by_id(self, transaction_id):
        for transaction in self._transactions:
            if transaction.id == transaction_id:
                return transaction
        return None

    async def get_user_transactions(self, *, user_id, page=1, page_size=20, status=None, category=None, order_by="created_at", descending=True):
        items = [tx for tx in self._transactions if tx.user_id == user_id]
        return items[(page - 1) * page_size : page * page_size], len(items)


def _build_wallet():
    wallet = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        currency="NGN",
        available_balance=Decimal("500.00"),
        ledger_balance=Decimal("500.00"),
        locked_balance=Decimal("0.00"),
        status="active",
        is_active=True,
        is_frozen=False,
        is_suspended=False,
    )
    return wallet


def _build_transactions(wallet):
    now = datetime.now(timezone.utc)
    return [
        SimpleNamespace(
            id=uuid4(),
            reference="txn-001",
            user_id=wallet.user_id,
            wallet_id=wallet.id,
            transaction_type="wallet_funding",
            category="funding",
            amount=Decimal("100.00"),
            currency="NGN",
            charges=Decimal("0.00"),
            total_amount=Decimal("100.00"),
            status="completed",
            description="Funding",
            created_at=now,
            updated_at=now,
            provider_name=None,
            provider_reference=None,
        ),
        SimpleNamespace(
            id=uuid4(),
            reference="txn-002",
            user_id=wallet.user_id,
            wallet_id=wallet.id,
            transaction_type="wallet_transfer_bank",
            category="transfer",
            amount=Decimal("25.00"),
            currency="NGN",
            charges=Decimal("1.25"),
            total_amount=Decimal("26.25"),
            status="completed",
            description="Transfer",
            created_at=now,
            updated_at=now,
            provider_name=None,
            provider_reference=None,
        ),
    ]


def test_get_wallet_statement_returns_filtered_summary() -> None:
    wallet = _build_wallet()
    transactions = _build_transactions(wallet)
    service = WalletStatementService(
        wallet_repository=DummyWalletRepository(wallet),
        transaction_repository=DummyTransactionRepository(transactions),
    )

    result = asyncio.run(
        service.get_wallet_statement(
            user_id=wallet.user_id,
            wallet_id=wallet.id,
            page=1,
            page_size=10,
        )
    )

    assert result["wallet_id"] == str(wallet.id)
    assert len(result["statement"]["transactions"]) == 2
    assert result["statement"]["summary"]["transaction_count"] == 2


def test_export_statement_csv_returns_csv_payload() -> None:
    wallet = _build_wallet()
    transactions = _build_transactions(wallet)
    service = WalletStatementService(
        wallet_repository=DummyWalletRepository(wallet),
        transaction_repository=DummyTransactionRepository(transactions),
    )

    result = asyncio.run(service.export_statement_csv(user_id=wallet.user_id, wallet_id=wallet.id))

    assert result["format"] == "csv"
    assert "reference" in result["payload"]
    assert "txn-001" in result["payload"]
