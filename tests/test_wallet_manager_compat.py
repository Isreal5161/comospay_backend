from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.wallet.wallet_manager import WalletManager


class DummyUserRepository:
    async def get_by_id(self, user_id):
        return SimpleNamespace(id=user_id, is_active=True)


class DummyWalletRepository:
    def __init__(self, wallet):
        self.wallet = wallet

    async def get_by_id(self, wallet_id):
        if self.wallet and self.wallet.id == wallet_id:
            return self.wallet
        return None

    async def get_by_id_for_update(self, wallet_id):
        return await self.get_by_id(wallet_id)

    async def get_user_wallet(self, *, user_id, wallet_type=None):
        if self.wallet and self.wallet.user_id == user_id:
            return self.wallet
        return None

    async def create_wallet(self, wallet):
        self.wallet = wallet
        return wallet

    async def update_wallet(self, wallet, **kwargs):
        return wallet


class DummySession:
    async def commit(self):
        return None

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_wallet_manager_exposes_balance_and_status_helpers():
    wallet_id = uuid4()
    user_id = uuid4()
    wallet = SimpleNamespace(
        id=wallet_id,
        user_id=user_id,
        wallet_reference="ref-001",
        wallet_type="customer",
        currency="NGN",
        status="active",
        available_balance=Decimal("10.00"),
        ledger_balance=Decimal("15.00"),
        locked_balance=Decimal("5.00"),
        is_active=True,
        is_frozen=False,
        is_suspended=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata_payload=None,
    )
    manager = WalletManager(
        user_repository=DummyUserRepository(),
        wallet_repository=DummyWalletRepository(wallet),
        virtual_account_service=object(),
        session=DummySession(),
    )

    balance = await manager.get_wallet_balance(wallet_id=wallet_id)
    assert balance["balance"]["available"] == "10.00"

    status = await manager.validate_wallet_status(wallet_id=wallet_id)
    assert status["valid"] is True
    assert status["status"] == "active"


@pytest.mark.asyncio
async def test_wallet_manager_wallet_exists_supports_wallet_type_filter():
    user_id = uuid4()
    wallet = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        wallet_reference="ref-002",
        wallet_type="customer",
        currency="NGN",
        status="active",
        available_balance=Decimal("0.00"),
        ledger_balance=Decimal("0.00"),
        locked_balance=Decimal("0.00"),
        is_active=True,
        is_frozen=False,
        is_suspended=False,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        metadata_payload=None,
    )
    manager = WalletManager(
        user_repository=DummyUserRepository(),
        wallet_repository=DummyWalletRepository(wallet),
        virtual_account_service=object(),
        session=DummySession(),
    )

    assert await manager.wallet_exists(user_id=user_id, wallet_type="customer") is True
    assert await manager.wallet_exists(user_id=uuid4(), wallet_type="customer") is False
