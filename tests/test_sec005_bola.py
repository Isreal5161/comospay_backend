from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.virtual_account import VirtualAccount
from app.models.wallet import Wallet
from app.services.virtual_account_service import VirtualAccountService
from app.services.wallet.wallet_manager import WalletManager
from app.utils.exceptions import ValidationException, WalletException


class FakeSession:
    async def commit(self):
        return None


class FakeWalletRepository:
    def __init__(self, wallet: Wallet | None = None):
        self.wallet = wallet

    async def get_by_id(self, wallet_id):
        if self.wallet and self.wallet.id == wallet_id:
            return self.wallet
        return None

    async def get_user_wallet(self, *, user_id, wallet_type=None):
        if self.wallet and self.wallet.user_id == user_id:
            return self.wallet
        return None

    async def get_by_id_for_update(self, wallet_id):
        return await self.get_by_id(wallet_id)

    async def update_wallet(self, wallet, **fields):
        for key, value in fields.items():
            setattr(wallet, key, value)
        return wallet


class FakeVirtualAccountRepository:
    def __init__(self, account: VirtualAccount):
        self.account = account

    async def get_by_id(self, virtual_account_id):
        if self.account.id == virtual_account_id:
            return self.account
        return None


class FakeUserRepository:
    async def get_by_id(self, user_id):
        return object()


@pytest.mark.asyncio
async def test_wallet_manager_rejects_other_users_wallet_lookup() -> None:
    owner_id = uuid4()
    attacker_id = uuid4()
    wallet = Wallet(
        id=uuid4(),
        user_id=owner_id,
        wallet_reference="wallet-1",
        wallet_type="customer",
        currency="NGN",
        available_balance=Decimal("0"),
        ledger_balance=Decimal("0"),
        locked_balance=Decimal("0"),
        metadata_payload=None,
    )
    manager = WalletManager(
        user_repository=FakeUserRepository(),
        wallet_repository=FakeWalletRepository(wallet),
        session=FakeSession(),
    )

    with pytest.raises((ValidationException, WalletException)):
        await manager.get_wallet(wallet_id=wallet.id, user_id=attacker_id)


@pytest.mark.asyncio
async def test_virtual_account_service_rejects_other_users_account_lookup() -> None:
    owner_id = uuid4()
    attacker_id = uuid4()
    account = VirtualAccount(
        id=uuid4(),
        wallet_id=uuid4(),
        user_id=owner_id,
        provider="flutterwave",
        account_number="1234567890",
        account_name="Owner Account",
        bank_name="Test Bank",
        provider_reference="provider-ref-1",
        currency="NGN",
        status="ACTIVE",
        kyc_status="VERIFIED",
        verification_status="VERIFIED",
        is_active=True,
        is_primary=True,
        metadata={},
    )
    service = VirtualAccountService(
        virtual_account_repository=FakeVirtualAccountRepository(account),
        wallet_repository=FakeWalletRepository(),
        user_repository=FakeUserRepository(),
        session=FakeSession(),
    )

    with pytest.raises((ValidationException, WalletException)):
        await service.get_virtual_account(virtual_account_id=account.id, user_id=attacker_id)
