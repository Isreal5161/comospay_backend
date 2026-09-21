from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.bank_account import BankAccount
from app.services.security.bank_account_encryption import BankAccountEncryption

TEST_PROJECT_REF = "dqptbbwbrrtfkjjhqbug"


def require_test_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    parsed = urlsplit(value)
    if not value.startswith("postgresql+asyncpg://") or parsed.hostname is None:
        raise RuntimeError("TEST_DATABASE_URL must be an explicit PostgreSQL asyncpg URL.")
    if "pooler.supabase.com" not in parsed.hostname or TEST_PROJECT_REF not in (parsed.username or ""):
        raise RuntimeError("TEST_DATABASE_URL must identify the dedicated test database.")
    if os.environ.get("DATABASE_URL", "").strip():
        raise RuntimeError("DATABASE_URL must not be present during the test backfill.")
    return value


async def backfill(session: AsyncSession, encryption: BankAccountEncryption) -> int:
    result = await session.execute(select(BankAccount).order_by(BankAccount.id).with_for_update())
    migrated = 0
    for account in result.scalars():
        stored = account.account_number_encrypted
        if encryption.is_encrypted(stored):
            if account.provider_reference and account.provider_reference.startswith("local:"):
                account.provider_reference = f"local:{uuid4()}"
            if account.provider_customer_reference and account.provider_customer_reference.startswith("cust:"):
                account.provider_customer_reference = f"cust:{uuid4()}"
            continue
        if not stored:
            raise RuntimeError("Bank account record has no account number to migrate.")
        encrypted = encryption.encrypt(stored)
        if encryption.decrypt(encrypted) != stored:
            raise RuntimeError("Bank account encryption verification failed.")
        account.account_number_encrypted = encrypted
        account.account_number_fingerprint = encryption.fingerprint(stored)
        account.account_number_prefix = stored[:2]
        account.account_number_last4 = stored[-4:]
        if account.provider_reference and account.provider_reference.startswith("local:"):
            account.provider_reference = f"local:{uuid4()}"
        if account.provider_customer_reference and account.provider_customer_reference.startswith("cust:"):
            account.provider_customer_reference = f"cust:{uuid4()}"
        migrated += 1
    await session.commit()
    return migrated


async def main() -> None:
    engine = create_async_engine(require_test_url(), pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            migrated = await backfill(session, BankAccountEncryption())
            print(f"Migrated bank-account records: {migrated}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
