from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import MetaData, make_url, text
from sqlalchemy.exc import ArgumentError
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


def _configured_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_path = BACKEND_ROOT / ".env"
    if not env_path.is_file():
        return ""
    return str(dotenv_values(env_path).get(name) or "").strip()


def _database_target(value: str, *, label: str) -> tuple[str, int, str, str]:
    try:
        parsed = make_url(value)
    except (ArgumentError, ValueError) as exc:
        raise SystemExit(f"{label} is malformed.") from exc

    username = parsed.username or ""
    project_ref = username.rsplit(".", 1)[-1] if "." in username else ""
    if (
        parsed.drivername != "postgresql+asyncpg"
        or parsed.host is None
        or parsed.port is None
        or parsed.database is None
    ):
        raise SystemExit(f"{label} must be a complete PostgreSQL asyncpg URL.")
    if "pooler.supabase.com" not in parsed.host:
        raise SystemExit(f"{label} must target a Supabase session pooler.")
    if not project_ref:
        raise SystemExit(f"{label} is missing its pooler project reference.")
    return parsed.host.lower(), parsed.port, parsed.database, project_ref


def require_test_url() -> str:
    value = _configured_value("TEST_DATABASE_URL")
    if not value:
        raise SystemExit("TEST_DATABASE_URL must be configured in the process environment or backend .env.")
    if not value.startswith("postgresql+asyncpg://"):
        raise SystemExit("TEST_DATABASE_URL must be an explicit PostgreSQL asyncpg URL.")
    test_target = _database_target(value, label="TEST_DATABASE_URL")
    if test_target[3] != TEST_PROJECT_REF:
        raise SystemExit("TEST_DATABASE_URL does not identify the dedicated test project.")

    application_value = _configured_value("DATABASE_URL")
    if not application_value:
        raise SystemExit("DATABASE_URL must be configured to verify test database isolation.")
    application_target = _database_target(application_value, label="DATABASE_URL")
    if test_target == application_target:
        raise SystemExit("Refusing to run because TEST_DATABASE_URL matches DATABASE_URL.")
    if test_target[3] == application_target[3]:
        raise SystemExit("Refusing to run because TEST_DATABASE_URL shares the application project reference.")
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
    if url != require_test_url():
        raise SystemExit("Refusing to initialize an unvalidated test database target.")
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
        env=_alembic_environment(url),
        check=True,
    )


def _alembic_environment(validated_test_url: str) -> dict[str, str]:
    child_env = dict(os.environ)
    child_env["DATABASE_URL"] = ""
    child_env["TEST_DATABASE_URL"] = validated_test_url
    child_env["MIGRATION_DATABASE_URL"] = validated_test_url
    return child_env


if __name__ == "__main__":
    main()
