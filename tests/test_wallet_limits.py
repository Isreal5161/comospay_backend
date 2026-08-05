import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.wallet.limits import WalletLimitsService
from app.utils.exceptions import ValidationException


class FakeSession:
    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


class FakeTransactionRepository:
    def __init__(self) -> None:
        self.session = FakeSession()


class FakeUserRepository:
    def __init__(self, user: SimpleNamespace) -> None:
        self._user = user

    async def get_by_id(self, user_id):
        return self._user if self._user.id == user_id else None


class FakeWalletRepository:
    def __init__(self, wallet: SimpleNamespace) -> None:
        self._wallet = wallet

    async def get_by_id(self, wallet_id):
        return self._wallet if self._wallet.id == wallet_id else None

    async def get_user_wallet(self, *, user_id, wallet_type=None):
        return self._wallet if self._wallet.user_id == user_id else None


class FakeSystemSettingsRepository:
    def __init__(self) -> None:
        self._settings = {}

    async def get_by_key(self, key: str):
        return self._settings.get(key)

    async def create_setting(self, setting):
        self._settings[setting.key] = setting
        return setting

    async def update_setting(self, setting_id, **kwargs):
        setting = self._settings.get(setting_id)
        if setting is None:
            return None
        for key, value in kwargs.items():
            setattr(setting, key, value)
        return setting


def test_get_user_limits_and_daily_validation() -> None:
    user_id = uuid4()
    wallet_id = uuid4()
    user = SimpleNamespace(id=user_id, kyc_level="verified")
    wallet = SimpleNamespace(id=wallet_id, user_id=user_id, available_balance=Decimal("1000.00"), ledger_balance=Decimal("1000.00"))

    service = WalletLimitsService(
        user_repository=FakeUserRepository(user),
        wallet_repository=FakeWalletRepository(wallet),
        system_settings_repository=FakeSystemSettingsRepository(),
        transaction_repository=FakeTransactionRepository(),
    )

    limits = asyncio.run(service.get_user_limits(user_id=user_id, wallet_id=wallet_id))
    assert Decimal(limits["per_transaction_limit"]) > Decimal("100000.00")

    with pytest.raises(ValidationException):
        asyncio.run(service.validate_daily_limit(user_id=user_id, wallet_id=wallet_id, amount=Decimal("5000000.00")))
