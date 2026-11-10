from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.ledger import Ledger
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.data.purchase import DataPurchaseService
from app.services.ledger_service import LedgerService
from app.services.payment.payment import PaymentManager
from app.services.payment.webhook import PaymentWebhookService
from app.utils.exceptions import PaymentException

TEST_PROJECT_REF = "dqptbbwbrrtfkjjhqbug"


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL is not configured for PostgreSQL verification.")
    if TEST_PROJECT_REF not in url.split("@", 1)[0]:
        pytest.skip("TEST_DATABASE_URL does not identify the dedicated PostgreSQL test project.")
    if os.environ.get("DATABASE_URL", "").strip():
        pytest.fail("DATABASE_URL must not be present during PostgreSQL concurrency tests.")
    return url


class PaymentProvider:
    async def execute_payment(self, **kwargs):
        await asyncio.sleep(0.1)
        return {
            "status": "succeeded",
            "provider": "flutterwave",
            "provider_reference": "integration-provider-reference",
            "provider_transaction_id": "integration-provider-transaction",
        }


class PendingDataProviderManager:
    async def execute(self, operation: str, **kwargs):
        return {
            "provider": "synthetic-test-provider",
            "data": {"transaction_data": {"status": "pending"}},
        }


class FakeWalletService:
    def __init__(self, wallet_repository: WalletRepository) -> None:
        self.wallet_manager = SimpleNamespace(wallet_repository=wallet_repository)

    async def verify_transaction_pin(self, *, user_id, pin):
        return None


async def _create_payment_fixture(
    session: AsyncSession,
    reference: str,
) -> tuple[User, Wallet, Transaction]:
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
        reference=reference,
        user_id=user.id,
        wallet_id=wallet.id,
        transaction_type="wallet_funding",
        category="deposit",
        amount=Decimal("100.00"),
        currency="NGN",
        charges=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        status="pending",
        provider_name="flutterwave",
    )
    session.add(transaction)
    await session.commit()
    return user, wallet, transaction


def _payment_services(session: AsyncSession):
    transaction_repository = TransactionRepository(session)
    wallet_repository = WalletRepository(session)
    user_repository = UserRepository(session)
    ledger_service = LedgerService(
        ledger_repository=LedgerRepository(session),
        wallet_repository=wallet_repository,
        user_repository=user_repository,
        transaction_repository=transaction_repository,
    )
    payment_manager = PaymentManager(
        user_repository=user_repository,
        wallet_repository=wallet_repository,
        transaction_repository=transaction_repository,
        provider_service=PaymentProvider(),
        ledger_service=ledger_service,
    )
    webhook_service = PaymentWebhookService(
        transaction_repository=transaction_repository,
        wallet_repository=wallet_repository,
        provider_service=PaymentProvider(),
        ledger_service=ledger_service,
    )
    return payment_manager, webhook_service


@pytest.mark.asyncio
async def test_postgres_verification_and_webhook_share_exactly_once_settlement():
    url = _test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=3)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    reference = f"FIN-INT-PAY-{uuid4().hex[:16].upper()}"
    user_id = wallet_id = transaction_id = None
    try:
        async with session_factory() as setup:
            user, wallet, transaction = await _create_payment_fixture(setup, reference)
            user_id, wallet_id, transaction_id = user.id, wallet.id, transaction.id

        payload = {
            "event": "charge.completed",
            "event_id": f"evt-{uuid4().hex}",
            "reference": reference,
            "status": "successful",
            "provider_reference": "integration-provider-reference",
        }
        timestamp = str(int(time.time()))
        signature = hmac.new(
            b"postgres-test-secret",
            json.dumps(payload, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        async with session_factory() as verification_session, session_factory() as webhook_session:
            payment_manager, _ = _payment_services(verification_session)
            _, webhook_service = _payment_services(webhook_session)

            async def verify():
                return await payment_manager.verify_payment(
                    reference=reference,
                    provider_operation=lambda _provider: None,
                )

            async def webhook():
                return await webhook_service.process_payment_webhook(
                    provider_name="flutterwave",
                    event_id=payload["event_id"],
                    payload=payload,
                    signature=signature,
                    timestamp=timestamp,
                    secret="postgres-test-secret",
                )

            await asyncio.gather(verify(), webhook())

        async with session_factory() as verify_session:
            wallet = await verify_session.get(Wallet, wallet_id)
            transaction = await verify_session.get(Transaction, transaction_id)
            ledger_count = await verify_session.scalar(
                select(func.count(Ledger.ledger_id)).where(
                    Ledger.related_transaction_id == transaction_id
                )
            )
            assert transaction.status == "succeeded"
            assert wallet.available_balance == wallet.ledger_balance == Decimal("100.00")
            assert ledger_count == 1
    finally:
        if user_id is not None and wallet_id is not None and transaction_id is not None:
            async with session_factory() as cleanup:
                async with cleanup.begin():
                    await cleanup.execute(delete(Ledger).where(Ledger.related_transaction_id == transaction_id))
                    await cleanup.execute(delete(Transaction).where(Transaction.id == transaction_id))
                    await cleanup.execute(delete(Wallet).where(Wallet.id == wallet_id))
                    await cleanup.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_data_purchase_wallet_lock_blocks_concurrent_duplicate_fulfilment():
    url = _test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15}, pool_size=3)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    user_id = wallet_id = None
    try:
        async with session_factory() as setup:
            user = User(email=f"{uuid4().hex}@example.test", username=uuid4().hex, status="active")
            setup.add(user)
            await setup.flush()
            wallet = Wallet(
                user_id=user.id,
                wallet_reference=f"wallet-{uuid4().hex}",
                currency="NGN",
                available_balance=Decimal("500.00"),
                ledger_balance=Decimal("500.00"),
                locked_balance=Decimal("0.00"),
            )
            setup.add(wallet)
            await setup.commit()
            user_id, wallet_id = user.id, wallet.id

        async with session_factory() as first_session, session_factory() as second_session:
            def service_for(session):
                transaction_repository = TransactionRepository(session)
                wallet_repository = WalletRepository(session)
                user_repository = UserRepository(session)
                ledger_service = LedgerService(
                    ledger_repository=LedgerRepository(session),
                    wallet_repository=wallet_repository,
                    user_repository=user_repository,
                    transaction_repository=transaction_repository,
                )
                return DataPurchaseService(
                    wallet_service=FakeWalletService(wallet_repository),
                    provider_service=SimpleNamespace(),
                    provider_manager=PendingDataProviderManager(),
                    transaction_repository=transaction_repository,
                    user_repository=user_repository,
                    ledger_service=ledger_service,
                )

            service_a = service_for(first_session)
            service_b = service_for(second_session)

            async def purchase(service):
                return await service.purchase_data(
                    user_id=user_id,
                    phone_number="08030000000",
                    network="MTN",
                    bundle_code="MTN-1GB",
                    amount=Decimal("75.00"),
                    transaction_pin="1234",
                )

            results = await asyncio.gather(purchase(service_a), purchase(service_b), return_exceptions=True)
            assert sum(isinstance(result, dict) for result in results) == 1
            assert sum(isinstance(result, PaymentException) for result in results) == 1

        async with session_factory() as verify_session:
            wallet = await verify_session.get(Wallet, wallet_id)
            transactions = list(
                (
                    await verify_session.execute(
                        select(Transaction).where(Transaction.user_id == user_id)
                    )
                ).scalars()
            )
            entries = list(
                (
                    await verify_session.execute(
                        select(Ledger).where(Ledger.user_id == user_id)
                    )
                ).scalars()
            )
            assert len(transactions) == 1
            assert transactions[0].status == "pending"
            assert wallet.available_balance == wallet.ledger_balance == Decimal("425.00")
            assert len(entries) == 1
            assert entries[0].entry_type == "DEBIT"
    finally:
        if user_id is not None and wallet_id is not None:
            async with session_factory() as cleanup:
                async with cleanup.begin():
                    await cleanup.execute(delete(Ledger).where(Ledger.user_id == user_id))
                    await cleanup.execute(delete(Transaction).where(Transaction.user_id == user_id))
                    await cleanup.execute(delete(Wallet).where(Wallet.id == wallet_id))
                    await cleanup.execute(delete(User).where(User.id == user_id))
        await engine.dispose()
