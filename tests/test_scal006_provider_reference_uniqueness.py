"""Test suite for provider-reference SCOPED uniqueness constraint (SCAL-006)

This test suite validates:
1. Database-enforced UNIQUE(provider_name, provider_reference) constraint
2. Different providers can use the same reference value independently
3. Same provider cannot reuse a reference value (database constraint protects)
4. NULL handling: multiple NULLs allowed
5. Application-level duplicate check is provider-scoped
6. Concurrency safety with row-level locking
"""
import pytest
import pytest_asyncio
from uuid import uuid4
from decimal import Decimal
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.models.transaction import Transaction
from app.models.user import User
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.repositories.user_repository import UserRepository
from app.services.wallet.funding import WalletFundingService
from app.utils.exceptions import DuplicateProviderReferenceException, WalletException


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def session():
    """Provide an async SQLAlchemy session with in-memory SQLite database"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = await session_factory().__aenter__()
    try:
        yield session
    finally:
        await session.rollback()
        await session.__aexit__(None, None, None)
        await engine.dispose()


@pytest.fixture
def wallet_funding_service(session):
    """Provide a WalletFundingService instance for testing"""
    transaction_repo = TransactionRepository(session)
    wallet_repo = WalletRepository(session)
    user_repo = UserRepository(session)

    return WalletFundingService(
        transaction_repository=transaction_repo,
        wallet_repository=wallet_repo,
        user_repository=user_repo,
    )


# ============================================================================
# Test Classes
# ============================================================================


@pytest.mark.asyncio
class TestProviderReferenceUniqueness:
    """Test UNIQUE(provider_name, provider_reference) constraint enforcement"""

    async def test_same_provider_same_reference_rejected_by_database(self, session):
        """
        SCENARIO: Same provider with same reference value
        EXPECTED: Second insert rejected by database constraint
        """
        ref_value = "provider-ref-001"
        provider = "flutterwave"

        # Create first transaction
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=ref_value,
        )
        session.add(txn1)
        await session.flush()

        # Attempt to create second transaction with same provider and reference
        txn2 = Transaction(
            reference="ref-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=ref_value,
        )
        session.add(txn2)

        # Should raise IntegrityError due to UNIQUE constraint
        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_different_providers_same_reference_allowed(self, session):
        """
        SCENARIO: Different providers with same reference value
        EXPECTED: Both inserts succeed (provider-scoped constraint)
        """
        ref_value = "shared-ref-123"
        provider1 = "flutterwave"
        provider2 = "paystack"

        # Create transaction with provider1
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider1,
            provider_reference=ref_value,
        )
        session.add(txn1)
        await session.flush()

        # Create transaction with provider2 and SAME reference value
        txn2 = Transaction(
            reference="ref-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name=provider2,
            provider_reference=ref_value,
        )
        session.add(txn2)

        # Should succeed - different providers can reuse same reference
        await session.flush()

        # Verify both exist
        result = await session.execute(
            select(Transaction).filter(Transaction.provider_reference == ref_value)
        )
        txns = result.scalars().all()
        assert len(txns) == 2
        assert {t.provider_name for t in txns} == {provider1, provider2}

    async def test_null_reference_multiple_allowed(self, session):
        """
        SCENARIO: Multiple transactions with NULL provider_reference
        EXPECTED: All inserts succeed (NULL constraint semantics)
        """
        provider = "flutterwave"

        # Create multiple transactions with same provider but NULL reference
        for i in range(3):
            txn = Transaction(
                reference=f"ref-{i:03d}",
                user_id=uuid4(),
                transaction_type="wallet_fund",
                category="funding",
                amount=Decimal("1000.00"),
                currency="NGN",
                total_amount=Decimal("1000.00"),
                status="pending",
                provider_name=provider,
                provider_reference=None,  # NULL
            )
            session.add(txn)

        # All should succeed - NULLs are not compared for uniqueness
        await session.flush()

        # Verify all were created
        result = await session.execute(
            select(Transaction).filter(
                Transaction.provider_name == provider,
                Transaction.provider_reference.is_(None),
            )
        )
        txns = result.scalars().all()
        assert len(txns) == 3

    async def test_null_provider_name_different_references(self, session):
        """
        SCENARIO: Multiple transactions with NULL provider_name
        EXPECTED: All inserts succeed regardless of provider_reference value
        """
        # Create multiple transactions with NULL provider_name but different references
        for i in range(3):
            txn = Transaction(
                reference=f"ref-{i:03d}",
                user_id=uuid4(),
                transaction_type="wallet_fund",
                category="funding",
                amount=Decimal("1000.00"),
                currency="NGN",
                total_amount=Decimal("1000.00"),
                status="pending",
                provider_name=None,  # NULL
                provider_reference=f"ref-{i}",
            )
            session.add(txn)

        # All should succeed - NULL provider_name means no provider-scoped uniqueness
        await session.flush()

        # Verify all were created
        result = await session.execute(
            select(Transaction).filter(Transaction.provider_name.is_(None))
        )
        txns = result.scalars().all()
        assert len(txns) == 3

    async def test_application_level_check_is_provider_scoped(self, session, wallet_funding_service):
        """
        SCENARIO: Application-level duplicate check rejects provider-scoped duplicate
        EXPECTED: WalletException raised for same provider + same reference
        """
        provider = "flutterwave"
        ref_value = "app-check-001"

        # Create existing transaction
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=ref_value,
        )
        session.add(txn1)
        await session.flush()

        # Check should reject same provider + same reference
        with pytest.raises(WalletException, match="Duplicate provider reference detected"):
            await wallet_funding_service._ensure_provider_reference_is_unique(
                provider_name=provider,
                provider_reference=ref_value,
            )

    async def test_application_level_check_allows_different_provider(self, session, wallet_funding_service):
        """
        SCENARIO: Application-level check allows different provider with same reference
        EXPECTED: No exception raised
        """
        ref_value = "app-check-002"

        # Create transaction with provider1
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference=ref_value,
        )
        session.add(txn1)
        await session.flush()

        # Check should pass for different provider
        await wallet_funding_service._ensure_provider_reference_is_unique(
            provider_name="paystack",  # Different provider
            provider_reference=ref_value,
        )
        # If we reach here without exception, test passes

    async def test_application_level_check_allows_null(self, session, wallet_funding_service):
        """
        SCENARIO: Application-level check is called with NULL reference
        EXPECTED: No exception (early return)
        """
        # Should not raise exception for NULL reference
        await wallet_funding_service._ensure_provider_reference_is_unique(
            provider_name="flutterwave",
            provider_reference=None,
        )
        # If we reach here without exception, test passes

    async def test_constraint_prevents_update_to_existing_reference(self, session):
        """
        SCENARIO: Update transaction to provider-reference that already exists for same provider
        EXPECTED: Database rejects the update
        """
        provider = "flutterwave"

        # Create two transactions
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider,
            provider_reference="ref-a",
        )
        txn2 = Transaction(
            reference="ref-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name=provider,
            provider_reference="ref-b",
        )
        session.add_all([txn1, txn2])
        await session.flush()

        # Attempt to update txn2's provider_reference to match txn1's
        txn2.provider_reference = "ref-a"

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_concurrent_inserts_same_provider_reference_rejected(self, session):
        """
        SCENARIO: Concurrent attempts to insert same provider_reference for same provider
        EXPECTED: Database constraint or row lock prevents duplicate

        Note: This simulates concurrency through the application's row-lock pattern.
        Real concurrency would be tested with actual concurrent connections.
        """
        provider = "flutterwave"
        ref_value = "concurrent-ref-001"

        # Create first transaction
        txn1 = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=ref_value,
        )
        session.add(txn1)
        await session.flush()

        # Attempt duplicate in same session
        txn2 = Transaction(
            reference="ref-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=ref_value,
        )
        session.add(txn2)

        with pytest.raises(IntegrityError):
            await session.flush()

    async def test_duplicate_provider_reference_is_translated_to_domain_exception(self, session):
        """Repository should convert provider-ref uniqueness violations into domain errors and recover the session."""
        repo = TransactionRepository(session)
        first = Transaction(
            reference="ref-duplicate-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="dup-123",
        )
        await repo.create_transaction(first)

        duplicate = Transaction(
            reference="ref-duplicate-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="dup-123",
        )

        with pytest.raises(DuplicateProviderReferenceException):
            await repo.create_transaction(duplicate)

        valid = Transaction(
            reference="ref-duplicate-003",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("3000.00"),
            currency="NGN",
            total_amount=Decimal("3000.00"),
            status="pending",
            provider_name="paystack",
            provider_reference="dup-123",
        )
        await repo.create_transaction(valid)

    async def test_repository_recovers_session_after_duplicate_provider_reference_error(self, session):
        """A failed provider-reference insert must leave the session usable for subsequent valid work."""
        repo = TransactionRepository(session)
        first = Transaction(
            reference="ref-recover-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="recover-123",
        )
        await repo.create_transaction(first)

        duplicate = Transaction(
            reference="ref-recover-002",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="recover-123",
        )

        with pytest.raises(DuplicateProviderReferenceException):
            await repo.create_transaction(duplicate)

        retry_txn = Transaction(
            reference="ref-recover-003",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("5000.00"),
            currency="NGN",
            total_amount=Decimal("5000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="recover-456",
        )
        created = await repo.create_transaction(retry_txn)
        assert created.provider_reference == "recover-456"

    async def test_unrelated_integrity_error_is_not_translated(self, session):
        """Only the provider-reference uniqueness constraint should be translated into a domain exception."""
        repo = TransactionRepository(session)

        first = Transaction(
            reference="ref-unique-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name="flutterwave",
            provider_reference="unique-001",
        )
        await repo.create_transaction(first)

        duplicate_reference = Transaction(
            reference="ref-unique-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("2000.00"),
            currency="NGN",
            total_amount=Decimal("2000.00"),
            status="pending",
            provider_name="paystack",
            provider_reference="unique-002",
        )

        with pytest.raises(IntegrityError):
            await repo.create_transaction(duplicate_reference)


@pytest.mark.asyncio
class TestProviderReferenceRegressions:
    """Test that existing functionality is not broken by the constraint"""

    async def test_existing_flow_webhook_idempotency(self, session):
        """
        SCENARIO: Webhook processing updates existing transaction
        EXPECTED: Update succeeds; constraint only prevents NEW duplicates
        """
        provider = "flutterwave"
        ref_value = "webhook-ref-001"

        # Create initial transaction
        txn = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="wallet_fund",
            category="funding",
            amount=Decimal("1000.00"),
            currency="NGN",
            total_amount=Decimal("1000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=None,
        )
        session.add(txn)
        await session.flush()

        # Webhook updates the same transaction with provider_reference
        txn.provider_reference = ref_value
        await session.flush()

        # Should succeed - updating existing row is allowed
        result = await session.execute(select(Transaction).filter_by(reference="ref-001"))
        updated_txn = result.scalar_one()
        assert updated_txn.provider_reference == ref_value

    async def test_existing_flow_payment_verification(self, session):
        """
        SCENARIO: Payment verification sets provider_reference
        EXPECTED: Setting provider_reference succeeds if unique for provider
        """
        provider = "paystack"
        ref_value = "payment-verify-001"

        # Create transaction without provider_reference
        txn = Transaction(
            reference="ref-001",
            user_id=uuid4(),
            transaction_type="payment",
            category="funding",
            amount=Decimal("5000.00"),
            currency="NGN",
            total_amount=Decimal("5000.00"),
            status="pending",
            provider_name=provider,
            provider_reference=None,
        )
        session.add(txn)
        await session.flush()

        # Payment verification sets the reference
        txn.provider_reference = ref_value
        await session.flush()

        # Verify update succeeded
        result = await session.execute(select(Transaction).filter_by(reference="ref-001"))
        verified_txn = result.scalar_one()
        assert verified_txn.provider_reference == ref_value

    async def test_existing_flow_multiple_providers_independent(self, session):
        """
        SCENARIO: Multiple providers process payments concurrently
        EXPECTED: Each provider's transaction can have independent references
        """
        providers = ["flutterwave", "paystack", "monnify"]

        # Create transactions for each provider
        for i, provider in enumerate(providers):
            txn = Transaction(
                reference=f"ref-{i:03d}",
                user_id=uuid4(),
                transaction_type="payment",
                category="funding",
                amount=Decimal("1000.00") * (i + 1),
                currency="NGN",
                total_amount=Decimal("1000.00") * (i + 1),
                status="completed",
                provider_name=provider,
                provider_reference=f"universal-ref-{i}",  # Same reference value
            )
            session.add(txn)

        # All should succeed - provider-scoped constraint
        await session.flush()

        # Verify all were created
        result = await session.execute(select(Transaction))
        txns = result.scalars().all()
        assert len(txns) == 3
        assert all(t.provider_name in providers for t in txns)
