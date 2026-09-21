from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.wallet.withdrawal import WalletWithdrawalService
from app.utils.exceptions import ProviderException, WalletException


class FakeWalletRepository:
    def __init__(self, wallet) -> None:
        self.wallet = wallet
        self.lock_calls = 0

    async def get_by_id_for_update(self, wallet_id):
        self.lock_calls += 1
        return self.wallet if wallet_id == self.wallet.id else None

    async def update_balance_fields(self, wallet, **fields):
        for field, value in fields.items():
            setattr(wallet, field, value)
        return wallet


class FakeTransactionRepository:
    def __init__(self, transaction) -> None:
        self.transaction = transaction
        self.lock_calls = 0
        self.updates: list[dict] = []

    async def get_by_id_for_update(self, transaction_id):
        self.lock_calls += 1
        return self.transaction if transaction_id == self.transaction.id else None

    async def get_by_reference_for_update(self, reference):
        self.lock_calls += 1
        return self.transaction if reference == self.transaction.reference else None

    async def update_transaction(self, transaction, **fields):
        self.updates.append(fields)
        for field, value in fields.items():
            setattr(transaction, field, value)
        return transaction


class FakeProviderService:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.kwargs: list[dict] = []

    async def execute_transfer(self, *, operation, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if self.error is not None:
            raise self.error
        return await operation(SimpleNamespace(code="flutterwave")) if self.result is None else self.result


class FakeProviderAdapter:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def transfer(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeBankAccountService:
    def __init__(self, user_id, bank_account_id) -> None:
        self.user_id = user_id
        self.bank_account_id = bank_account_id
        self.calls: list[dict] = []

    async def resolve_withdrawal_account(self, *, user_id, bank_account_id):
        self.calls.append({"user_id": user_id, "bank_account_id": bank_account_id})
        if user_id != self.user_id or bank_account_id != self.bank_account_id:
            raise WalletException("Bank account not found.")
        return {
            "id": bank_account_id,
            "user_id": user_id,
            "account_number": "0123456789",
            "bank_code": "044",
            "account_name": "Trusted Account",
        }


def build_context():
    user_id = uuid4()
    wallet_id = uuid4()
    bank_account_id = uuid4()
    transaction = SimpleNamespace(
        id=uuid4(),
        reference="wdl-stable-reference",
        user_id=user_id,
        wallet_id=wallet_id,
        bank_account_id=bank_account_id,
        category="withdrawal",
        transaction_type="wallet_withdrawal_bank",
        amount=Decimal("100000.00"),
        currency="NGN",
        status="funds_reserved",
        provider_name=None,
        provider_reference=None,
        provider_transaction_id=None,
        payout_amount=None,
        payout_currency=None,
        metadata_payload='{"bank_code":"044","account_number_last4":"6789"}',
    )
    wallet = SimpleNamespace(
        id=wallet_id,
        user_id=user_id,
        available_balance=Decimal("25000.00"),
        ledger_balance=Decimal("125000.00"),
        locked_balance=Decimal("100000.00"),
    )
    return user_id, transaction, wallet, bank_account_id


def build_service(result=None, error=None):
    user_id, transaction, wallet, bank_account_id = build_context()
    wallet_repository = FakeWalletRepository(wallet)
    transaction_repository = FakeTransactionRepository(transaction)
    provider_service = FakeProviderService(result=result, error=error)
    provider_adapter = FakeProviderAdapter(
        result={
            "provider": "flutterwave",
            "status": "success",
            "provider_reference": "fw-ref-1",
            "provider_transaction_id": "fw-tx-1",
            "amount": "100000.00",
            "currency": "NGN",
        }
    )
    bank_account_service = FakeBankAccountService(user_id, bank_account_id)
    service = WalletWithdrawalService(
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=provider_service,
        provider_adapter=provider_adapter,
        bank_account_service=bank_account_service,
    )
    return service, user_id, transaction, wallet, wallet_repository, transaction_repository, provider_service, provider_adapter, bank_account_service


@pytest.mark.asyncio
async def test_success_consumes_reservation_and_settles_once() -> None:
    service, user_id, transaction, wallet, wallet_repo, transaction_repo, provider_service, adapter, bank_service = build_service()

    result = await service.execute_withdrawal(
        transaction_id=transaction.id,
        authenticated_user_id=user_id,
    )

    assert result["status"] == "completed"
    assert wallet.available_balance == Decimal("25000.00")
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.ledger_balance == Decimal("25000.00")
    assert transaction.provider_reference == "fw-ref-1"
    assert transaction.provider_transaction_id == "fw-tx-1"
    assert transaction.payout_amount == Decimal("100000.00")
    assert wallet_repo.lock_calls == 1
    assert transaction_repo.lock_calls == 1
    assert provider_service.calls == 1
    assert len(adapter.calls) == 1
    assert bank_service.calls[0]["bank_account_id"] == transaction.bank_account_id

    duplicate = await service.execute_withdrawal(transaction_id=transaction.id)
    assert duplicate["status"] == "completed"
    assert provider_service.calls == 1


@pytest.mark.asyncio
async def test_execution_uses_provider_selected_adapter_factory() -> None:
    service, user_id, transaction, _, _, _, _, adapter, _ = build_service()
    service.provider_adapter = None
    selected_providers: list[str] = []

    def provider_factory(provider):
        selected_providers.append(provider.code)
        return adapter

    service.provider_adapter_factory = provider_factory

    result = await service.execute_withdrawal(
        transaction_id=transaction.id,
        authenticated_user_id=user_id,
    )

    assert result["status"] == "completed"
    assert selected_providers == ["flutterwave"]
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_confirmed_failure_releases_reservation_idempotently() -> None:
    service, user_id, transaction, wallet, _, _, provider_service, adapter, _ = build_service(
        result={"data": {"status": "failed", "provider_reference": "fw-ref-failed"}}
    )

    result = await service.execute_withdrawal(
        transaction_id=transaction.id,
        authenticated_user_id=user_id,
    )

    assert result["status"] == "failed"
    assert wallet.available_balance == Decimal("125000.00")
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.ledger_balance == Decimal("125000.00")
    assert provider_service.calls == 1
    assert len(adapter.calls) == 0

    duplicate = await service.execute_withdrawal(transaction_id=transaction.id)
    assert duplicate["status"] == "failed"
    assert wallet.available_balance == Decimal("125000.00")


@pytest.mark.asyncio
async def test_pending_keeps_reservation_and_provider_reference() -> None:
    service, user_id, transaction, wallet, _, _, provider_service, adapter, _ = build_service(
        result={
            "data": {
                "status": "pending",
                "provider_reference": "fw-ref-pending",
                "provider_transaction_id": "fw-tx-pending",
            }
        }
    )

    result = await service.execute_withdrawal(
        reference=transaction.reference,
        authenticated_user_id=user_id,
    )

    assert result["status"] == "pending"
    assert transaction.provider_reference == "fw-ref-pending"
    assert wallet.available_balance == Decimal("25000.00")
    assert wallet.locked_balance == Decimal("100000.00")
    assert wallet.ledger_balance == Decimal("125000.00")
    assert provider_service.calls == 1
    assert len(adapter.calls) == 0


@pytest.mark.asyncio
async def test_timeout_keeps_reservation_and_retry_reuses_reference() -> None:
    service, user_id, transaction, wallet, _, _, provider_service, adapter, _ = build_service(
        error=ProviderException("Flutterwave request timed out")
    )

    first = await service.execute_withdrawal(
        transaction_id=transaction.id,
        authenticated_user_id=user_id,
    )

    assert first["status"] == "pending"
    assert wallet.locked_balance == Decimal("100000.00")
    assert len(adapter.calls) == 0

    provider_service.error = None
    provider_service.result = None
    second = await service.execute_withdrawal(
        transaction_id=transaction.id,
        authenticated_user_id=user_id,
    )

    assert second["status"] == "completed"
    assert adapter.calls[0]["reference"] == "wdl-stable-reference"
    assert wallet.locked_balance == Decimal("0.00")
    assert wallet.available_balance == Decimal("25000.00")
    assert provider_service.kwargs[0]["retryable_errors"] == ()


@pytest.mark.asyncio
async def test_ownership_mismatch_cannot_execute_withdrawal() -> None:
    service, _, transaction, wallet, _, _, provider_service, _, _ = build_service()

    with pytest.raises(WalletException):
        await service.execute_withdrawal(
            transaction_id=transaction.id,
            authenticated_user_id=uuid4(),
        )

    assert provider_service.calls == 0
    assert wallet.locked_balance == Decimal("100000.00")


@pytest.mark.asyncio
async def test_execution_rejects_client_beneficiary_override() -> None:
    service, user_id, transaction, wallet, _, _, provider_service, _, _ = build_service()

    with pytest.raises(Exception, match="trusted bank account"):
        await service.execute_withdrawal(
            transaction_id=transaction.id,
            authenticated_user_id=user_id,
            account_details={"bank_code": "058", "account_number": "9999999999"},
        )

    assert provider_service.calls == 0
    assert wallet.locked_balance == Decimal("100000.00")