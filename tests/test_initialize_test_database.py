from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
INITIALIZER_PATH = BACKEND_ROOT / "tests" / "database" / "initialize_test_database.py"
SPEC = importlib.util.spec_from_file_location("initialize_test_database", INITIALIZER_PATH)
assert SPEC is not None and SPEC.loader is not None
initializer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = initializer
SPEC.loader.exec_module(initializer)


TEST_URL = "postgresql+asyncpg://postgres.dqptbbwbrrtfkjjhqbug:test-only@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"
APP_URL = "postgresql+asyncpg://postgres.applicationref123:test-only@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"


def _write_env(path: Path, *, test_url: str | None, app_url: str = APP_URL) -> None:
    lines = [f'DATABASE_URL="{app_url}"']
    if test_url is not None:
        lines.append(f'TEST_DATABASE_URL="{test_url}"')
    (path / ".env").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def isolated_backend_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(initializer, "BACKEND_ROOT", tmp_path)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    return tmp_path


def test_missing_test_url_aborts(isolated_backend_root: Path) -> None:
    _write_env(isolated_backend_root, test_url=None)

    with pytest.raises(SystemExit, match="TEST_DATABASE_URL must be configured"):
        initializer.require_test_url()


def test_malformed_test_url_aborts_before_database_work(isolated_backend_root: Path) -> None:
    _write_env(isolated_backend_root, test_url="postgresql+asyncpg://not a valid URL")

    with pytest.raises(SystemExit, match="malformed|complete PostgreSQL"):
        initializer.require_test_url()


def test_test_url_matching_application_target_aborts(isolated_backend_root: Path) -> None:
    same_target = (
        "postgresql+asyncpg://postgres.dqptbbwbrrtfkjjhqbug:test-only"
        "@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"
    )
    _write_env(isolated_backend_root, test_url=same_target, app_url=same_target)

    with pytest.raises(SystemExit, match="matches DATABASE_URL"):
        initializer.require_test_url()


@pytest.mark.parametrize(
    "username",
    ["postgres", "postgres.unverified-project"],
)
def test_missing_or_unverified_test_project_reference_aborts(
    isolated_backend_root: Path,
    username: str,
) -> None:
    test_url = f"postgresql+asyncpg://{username}:test-only@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"
    _write_env(isolated_backend_root, test_url=test_url)

    with pytest.raises(SystemExit, match="project reference|dedicated test project"):
        initializer.require_test_url()


def test_valid_isolated_test_url_loads_from_backend_env(isolated_backend_root: Path) -> None:
    _write_env(isolated_backend_root, test_url=TEST_URL)

    assert initializer.require_test_url() == TEST_URL


@pytest.mark.asyncio
async def test_create_baseline_refuses_unvalidated_target_before_engine_creation(
    isolated_backend_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_env(isolated_backend_root, test_url=TEST_URL)
    monkeypatch.setattr(
        initializer,
        "create_async_engine",
        lambda *args, **kwargs: pytest.fail("Database engine must not be created for an unvalidated URL."),
    )

    with pytest.raises(SystemExit, match="unvalidated test database target"):
        await initializer.create_baseline(APP_URL)


def test_process_environment_overrides_test_url_from_env_file(
    isolated_backend_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_test_url = TEST_URL.replace("aws-1-eu-west-1", "aws-2-eu-west-1")
    _write_env(isolated_backend_root, test_url=file_test_url)
    monkeypatch.setenv("TEST_DATABASE_URL", TEST_URL)

    assert initializer.require_test_url() == TEST_URL


def test_alembic_child_receives_validated_override_not_application_url(
    isolated_backend_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_env(isolated_backend_root, test_url=TEST_URL)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", APP_URL)
    baseline_calls: list[str] = []
    subprocess_calls: list[dict[str, object]] = []

    async def fake_create_baseline(url: str) -> None:
        baseline_calls.append(url)

    def fake_subprocess_run(*args: object, **kwargs: object) -> None:
        subprocess_calls.append({"args": args, **kwargs})

    monkeypatch.setattr(initializer, "create_baseline", fake_create_baseline)
    monkeypatch.setattr(initializer.subprocess, "run", fake_subprocess_run)

    initializer.main()

    assert baseline_calls == [TEST_URL]
    assert len(subprocess_calls) == 1
    child_env = subprocess_calls[0]["env"]
    assert isinstance(child_env, dict)
    assert child_env["MIGRATION_DATABASE_URL"] == TEST_URL
    assert child_env["TEST_DATABASE_URL"] == TEST_URL
    assert child_env["DATABASE_URL"] == ""
