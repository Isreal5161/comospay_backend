from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401 - register every ORM model with Base.metadata
from app.database.base import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TEST_PROJECT_REF = "dqptbbwbrrtfkjjhqbug"
MIGRATION_OWNED_COLUMNS = {
    "virtual_accounts": {"status", "retry_count", "last_retry_at", "next_retry_at", "last_error", "provisioned_at"},
    "transactions": {
        "card_amount",
        "card_currency",
        "payout_amount",
        "payout_currency",
        "credited_amount",
        "credited_currency",
        "credit_applied",
    },
}


def require_test_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not value.startswith("postgresql+asyncpg://"):
        raise SystemExit("TEST_DATABASE_URL must be an explicit PostgreSQL asyncpg URL.")
    parsed = urlsplit(value)
    if parsed.hostname is None or "pooler.supabase.com" not in parsed.hostname:
        raise SystemExit("TEST_DATABASE_URL must target the Supabase session pooler.")
    if TEST_PROJECT_REF not in (parsed.username or ""):
        raise SystemExit("TEST_DATABASE_URL does not identify the dedicated test project.")
    production = os.environ.get("DATABASE_URL", "").strip()
    if production and production == value:
        raise SystemExit("Refusing to run when DATABASE_URL equals TEST_DATABASE_URL.")
    return value


def baseline_metadata() -> MetaData:
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)

    for table_name, columns in MIGRATION_OWNED_COLUMNS.items():
        table = metadata.tables[table_name]
        for index in list(table.indexes):
            if any(column.name in columns for column in index.columns):
                table.indexes.remove(index)
        for constraint in list(table.constraints):
            if any(column.name in columns for column in constraint.columns):
                table.constraints.remove(constraint)
        for column_name in columns:
            if column_name in table.c:
                table._columns.remove(table.c[column_name])

    transaction = metadata.tables["transactions"]
    for constraint in list(transaction.constraints):
        if constraint.name == "uq_transactions_provider_ref":
            transaction.constraints.remove(constraint)

    for table in metadata.tables.values():
        for index in list(table.indexes):
            if any(column.name not in table.c for column in index.columns):
                table.indexes.remove(index)
    return metadata


async def create_baseline(url: str) -> None:
    engine = create_async_engine(url, pool_pre_ping=True, connect_args={"ssl": "require", "timeout": 15})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(baseline_metadata().create_all)
            await connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS alembic_version (
                        version_num VARCHAR(255) NOT NULL PRIMARY KEY
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE alembic_version
                    ALTER COLUMN version_num TYPE VARCHAR(255)
                    """
                )
            )
    finally:
        await engine.dispose()


def main() -> None:
    url = require_test_url()
    print("TARGET DATABASE: TEST DATABASE")
    print("MIGRATION TARGET: TEST_DATABASE_URL")
    print("PRODUCTION DATABASE: NOT USED")
    asyncio.run(create_baseline(url))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "TEST_DATABASE_URL": url},
        check=True,
    )


if __name__ == "__main__":
    main()
