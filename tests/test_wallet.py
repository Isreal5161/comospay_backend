import pytest
from decimal import Decimal
from uuid import uuid4

from app.services.wallet.funding import WalletFundingService
from app.utils.exceptions import ValidationException, WalletException


class DummyRepository:
    def __init__(self, item=None):
        self.item = item
        self.calls = []

    async def get_by_id(self, _id):
        return self.item

    async def get_by_reference(self, _reference):
        return self.item

    async def create_transaction(self, transaction):
        self.calls.append(("create_transaction", transaction))
        return transaction

    async def update_transaction(self, transaction, **fields):
        self.calls.append(("update_transaction", transaction, fields))
        return transaction

    async def update_balance_fields(self, wallet, **fields):
        self.calls.append(("update_balance_fields", wallet, fields))
        return wallet


class DummyWalletRepository(DummyRepository):
    async def get_by_id(self, wallet_id):
        return self.item


class DummyTransactionRepository(DummyRepository):
    pass


class DummyProviderService:
    async def initialize_funding(self, **kwargs):
        return {"provider_transaction_id": "prov-1", "status": "pending"}


class DummyWalletRepositoryWithLocking(DummyWalletRepository):
    async def get_by_id_for_update(self, wallet_id):
        return await self.get_by_id(wallet_id)


class DummyTransactionRepositoryWithoutSession(DummyTransactionRepository):
    def __init__(self, item=None):
        self.item = item
        self.calls = []
        self.session = None


@pytest.mark.asyncio
async def test_initialize_wallet_funding_rejects_inactive_wallet():
    user_id = uuid4()
    wallet = type(
        "Wallet",
        (),
        {
            "id": uuid4(),
            "user_id": user_id,
            "status": "active",
            "is_active": False,
            "is_frozen": False,
            "is_suspended": False,
            "currency": "NGN",
        },
    )()
    wallet_repository = DummyWalletRepository(wallet)
    transaction_repository = DummyTransactionRepository(None)
    service = WalletFundingService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=DummyProviderService(),
    )

    with pytest.raises(WalletException):
        await service.initialize_wallet_funding(
            user_id=user_id,
            wallet_id=wallet.id,
            amount=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_initialize_wallet_funding_handles_repositories_without_session():
    user_id = uuid4()
    wallet = type(
        "Wallet",
        (),
        {
            "id": uuid4(),
            "user_id": user_id,
            "status": "active",
            "is_active": True,
            "is_frozen": False,
            "is_suspended": False,
            "currency": "NGN",
            "available_balance": Decimal("0.00"),
            "ledger_balance": Decimal("0.00"),
            "locked_balance": Decimal("0.00"),
        },
    )()
    wallet_repository = DummyWalletRepositoryWithLocking(wallet)
    transaction_repository = DummyTransactionRepositoryWithoutSession(None)
    service = WalletFundingService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=DummyProviderService(),
    )

    result = await service.initialize_wallet_funding(
        user_id=user_id,
        wallet_id=wallet.id,
        amount=Decimal("100"),
    )

    assert result["status"] == "pending"
    assert result["wallet_id"] == str(wallet.id)
