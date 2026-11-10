from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.ledger import Ledger
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.ledger_service import LedgerService
from app.services.payment.payment import PaymentManager


class SuccessfulProviderService:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_payment(self, **kwargs):
        self.calls += 1
        return {
            "status": "succeeded",
            "provider": "flutterwave",
            "provider_reference": "provider-reference",
            "provider_transaction_id": "provider-transaction",
        }


class FailingLedgerService:
    async def create_credit_entry(self, **kwargs):
        raise RuntimeError("controlled ledger failure")


@pytest_asyncio.fixture
async def payment_context():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with session_factory() as session:
        user = User(email=f"{uuid4().hex}@example.test", username=uuid4().hex, status="active")
        session.add(user)
        await session.flush()
        wallet = Wallet(
            user_id=user.id,
            wallet_reference=f"wallet-{uuid4().hex}",
            currency="NGN",
            available_balance=Decimal("0.00"),
            ledger_balance=Decimal("0.00"),
            locked_balance=Decimal("0.00"),
        )
        session.add(wallet)
        await session.flush()
        transaction = Transaction(
            reference=f"deposit-{uuid4().hex}",
            user_id=user.id,
            wallet_id=wallet.id,
            transaction_type="wallet_funding",
            category="deposit",
            amount=Decimal("75.00"),
            currency="NGN",
            charges=Decimal("0.00"),
            total_amount=Decimal("75.00"),
            status="pending",
            provider_name="flutterwave",
            description="Test wallet funding",
        )
        session.add(transaction)
        await session.commit()

        yield session, transaction.reference, wallet.id, transaction.id, session_factory

    await engine.dispose()


def build_payment_manager(session, *, provider_service, ledger_service=None) -> PaymentManager:
    transaction_repository = TransactionRepository(session)
    wallet_repository = WalletRepository(session)
    user_repository = UserRepository(session)
    if ledger_service is None:
        ledger_service = LedgerService(
            ledger_repository=LedgerRepository(session),
            wallet_repository=wallet_repository,
            user_repository=user_repository,
            transaction_repository=transaction_repository,
        )
    return PaymentManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=provider_service,
        ledger_service=ledger_service,
    )


@pytest.mark.asyncio
async def test_successful_payment_verification_credits_wallet_and_creates_one_ledger_entry(payment_context):
    session, reference, wallet_id, transaction_id, _ = payment_context
    provider = SuccessfulProviderService()
    manager = build_payment_manager(session, provider_service=provider)

    first = await manager.verify_payment(reference=reference, provider_operation=lambda _provider: None)
    second = await manager.verify_payment(reference=reference, provider_operation=lambda _provider: None)

    wallet = await session.get(Wallet, wallet_id)
    entries = list(
        (
            await session.execute(
                select(Ledger).where(Ledger.related_transaction_id == transaction_id)
            )
        ).scalars()
    )
    transaction = await session.get(Transaction, transaction_id)

    assert first["status"] == second["status"] == "succeeded"
    assert provider.calls == 1
    assert wallet.available_balance == wallet.ledger_balance == Decimal("75.00")
    assert len(entries) == 1
    assert entries[0].entry_type == "CREDIT"
    assert entries[0].credit_amount == Decimal("75.00")
    assert entries[0].transaction_reference == reference
    assert entries[0].related_transaction_id == transaction_id
    assert json.loads(transaction.metadata_payload)["wallet_credit_applied"] is True


@pytest.mark.asyncio
async def test_ledger_failure_rolls_back_payment_wallet_and_transaction_changes(payment_context):
    session, reference, wallet_id, transaction_id, session_factory = payment_context
    manager = build_payment_manager(
        session,
        provider_service=SuccessfulProviderService(),
        ledger_service=FailingLedgerService(),
    )

    with pytest.raises(RuntimeError, match="controlled ledger failure"):
        await manager.verify_payment(reference=reference, provider_operation=lambda _provider: None)

    await session.rollback()
    async with session_factory() as verification_session:
        wallet = await verification_session.get(Wallet, wallet_id)
        transaction = await verification_session.get(Transaction, transaction_id)
        entries = list(
            (
                await verification_session.execute(
                    select(Ledger).where(Ledger.related_transaction_id == transaction_id)
                )
            ).scalars()
        )

    assert wallet.available_balance == wallet.ledger_balance == Decimal("0.00")
    assert transaction.status == "pending"
    assert entries == []
