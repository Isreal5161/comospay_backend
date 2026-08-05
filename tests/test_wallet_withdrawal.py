import asyncio
from types import SimpleNamespace

import pytest

from app.services.wallet.withdrawal import WalletWithdrawalService
from app.utils.exceptions import ValidationException


class DummyRepository(SimpleNamespace):
    pass


def test_validate_transaction_pin_accepts_numeric_pin() -> None:
    service = WalletWithdrawalService(
        wallet_repository=DummyRepository(),
        transaction_repository=DummyRepository(),
    )

    assert asyncio.run(service.validate_transaction_pin(transaction_pin="123456")) is True


def test_validate_transaction_pin_rejects_non_numeric_pin() -> None:
    service = WalletWithdrawalService(
        wallet_repository=DummyRepository(),
        transaction_repository=DummyRepository(),
    )

    with pytest.raises(ValidationException):
        asyncio.run(service.validate_transaction_pin(transaction_pin="abcd"))
