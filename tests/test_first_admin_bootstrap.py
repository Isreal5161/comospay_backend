from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from app import main as app_main
from sqlalchemy.exc import IntegrityError

from app.models.admin import Admin
from app.repositories.admin_repository import AdminRepository
from app.services.admin.staff import FirstAdminAlreadyExists, StaffAdministrationService
from app.services.auth.password_service import PasswordPolicyViolationException, PasswordService
from scripts import bootstrap_first_admin as bootstrap_module


TEST_PASSWORD = "TestOnly-Bootstrap-Pass9!"


class FakeTransaction:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc_type is not None:
            self.session.rollback_count += 1
        return False


class FakeSession:
    def __init__(self) -> None:
        self.begin_count = 0
        self.rollback_count = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False

    def begin(self) -> FakeTransaction:
        self.begin_count += 1
        return FakeTransaction(self)


class FakeSessionFactory:
    def __init__(self, *, before_second_session: Any = None) -> None:
        self.calls = 0
        self.before_second_session = before_second_session
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        self.calls += 1
        if self.calls == 2 and self.before_second_session is not None:
            self.before_second_session()
        session = FakeSession()
        self.sessions.append(session)
        return session


class FakeAdminRepository:
    def __init__(self, admins: list[Admin] | None = None, *, create_error: Exception | None = None) -> None:
        self.admins = list(admins or [])
        self.create_error = create_error
        self.lock_count = 0
        self.created: Admin | None = None

    async def get_all(self, *, page: int, page_size: int) -> tuple[list[Admin], int]:
        return self.admins[:page_size], len(self.admins)

    async def acquire_first_admin_bootstrap_lock(self) -> None:
        self.lock_count += 1

    async def create(self, admin: Admin) -> Admin:
        if self.create_error is not None:
            raise self.create_error
        self.created = admin
        self.admins.append(admin)
        return admin


def make_staff_service_builder(repository: FakeAdminRepository) -> Any:
    password_service = PasswordService()

    def build(session: FakeSession) -> StaffAdministrationService:
        return StaffAdministrationService(
            admin_repository=repository,  # type: ignore[arg-type]
            password_service=password_service,
            session=session,  # type: ignore[arg-type]
        )

    return build


@pytest.fixture
def bootstrap_dependencies(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeAdminRepository, FakeSessionFactory]:
    repository = FakeAdminRepository()
    factory = FakeSessionFactory()
    monkeypatch.setattr(bootstrap_module, "_build_staff_service", make_staff_service_builder(repository))
    return repository, factory


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["Yes", " yes", "yes ", "y", "no", ""])
async def test_non_exact_yes_confirmation_aborts_before_collecting_admin(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
    response: str,
) -> None:
    repository, factory = bootstrap_dependencies
    prompts: list[str] = []
    output: list[str] = []

    def prompt(message: str) -> str:
        prompts.append(message)
        return response

    def unexpected_secret_prompt(_: str) -> str:
        raise AssertionError("confirmation refusal must happen before password collection")

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=prompt,
        secret_prompt=unexpected_secret_prompt,
        output=output.append,
    )

    assert result != 0
    assert prompts == ["Continue? [yes/no]: "]
    assert factory.calls == 0
    assert repository.created is None
    assert output[-1] == "Bootstrap cancelled; no Admin was created."


@pytest.mark.asyncio
async def test_production_warning_displays_environment_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.settings import settings

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "database_url", "postgresql://db-user:db-secret@example.test/cosmozpay")
    output: list[str] = []
    factory = FakeSessionFactory()

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: "no",
        output=output.append,
    )

    assert result != 0
    assert "Environment: production" in output
    assert "WARNING: THIS IS A PRODUCTION DATABASE OPERATION." in output
    assert "postgresql://db-user:db-secret@example.test/cosmozpay" not in "\n".join(output)
    assert "db-secret" not in "\n".join(output)
    assert factory.calls == 0


@pytest.mark.asyncio
async def test_creates_first_admin_with_hashed_password_and_required_flags(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository, factory = bootstrap_dependencies
    prompts = iter(["yes", "  cosmozpay@example.com  ", " first-admin "])
    prompt_messages: list[str] = []
    secrets = iter([TEST_PASSWORD, TEST_PASSWORD])
    output: list[str] = []

    def prompt(message: str) -> str:
        prompt_messages.append(message)
        return next(prompts)

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=prompt,
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )

    assert result == 0
    assert prompt_messages[0] == "Continue? [yes/no]: "
    admin = repository.created
    assert admin is not None
    assert isinstance(admin, Admin)
    assert admin.email == "cosmozpay@example.com"
    assert admin.username == "first-admin"
    assert admin.role == "super_admin"
    assert admin.status == "active"
    assert admin.is_active is True
    assert admin.is_super_admin is True
    assert admin.mfa_enabled is False
    assert admin.two_factor_secret is None
    assert admin.password_hash != TEST_PASSWORD
    assert isinstance(admin, Admin)
    assert repository.admins == [admin]
    assert PasswordService().verify_password(TEST_PASSWORD, admin.password_hash or "")
    assert output == [
        "WARNING: This operation creates the FIRST SUPER ADMIN for the currently configured database.",
        "Environment: development",
        "First Admin created successfully.",
        "Email: cosmozpay@example.com",
        "Role: super_admin",
        "Status: active",
        "MFA enabled: false",
    ]
    assert all(TEST_PASSWORD not in line for line in output)
    assert TEST_PASSWORD not in caplog.text
    assert admin.password_hash not in "\n".join(output)
    assert admin.password_hash not in caplog.text
    assert repository.lock_count == 1
    assert all(session.begin_count == 1 for session in factory.sessions)


@pytest.mark.asyncio
async def test_existing_admin_refuses_before_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = Admin(email="existing@example.com", username="existing", password_hash="already-hashed")
    repository = FakeAdminRepository([existing])
    factory = FakeSessionFactory()
    monkeypatch.setattr(bootstrap_module, "_build_staff_service", make_staff_service_builder(repository))
    output: list[str] = []

    prompts = iter(["yes"])

    def prompt(_: str) -> str:
        return next(prompts)

    def unexpected_secret_prompt(_: str) -> str:
        raise AssertionError("existing Admin must be rejected before asking for credentials")

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=prompt,
        secret_prompt=unexpected_secret_prompt,
        output=output.append,
    )

    assert result != 0
    assert output[-1] == "Bootstrap refused: an Admin account already exists."
    assert repository.created is None
    assert repository.lock_count == 0


@pytest.mark.asyncio
async def test_locked_second_check_refuses_racing_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FakeAdminRepository()
    factory = FakeSessionFactory(
        before_second_session=lambda: repository.admins.append(
            Admin(email="racing@example.com", username="racing", password_hash="existing-hash")
        )
    )
    monkeypatch.setattr(bootstrap_module, "_build_staff_service", make_staff_service_builder(repository))
    prompts = iter(["yes", "first@example.com", "first-admin"])
    secrets = iter([TEST_PASSWORD, TEST_PASSWORD])
    output: list[str] = []

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )

    assert result != 0
    assert output[-1] == "Bootstrap refused: an Admin account already exists."
    assert repository.created is None
    assert repository.lock_count == 1


@pytest.mark.asyncio
async def test_password_confirmation_mismatch_fails_without_creation(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
) -> None:
    repository, factory = bootstrap_dependencies
    prompts = iter(["yes", "first@example.com", "first-admin"])
    secrets = iter([TEST_PASSWORD, "Different-Test-Password9!"])
    output: list[str] = []

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )

    assert result != 0
    assert repository.created is None
    assert any("confirmation" in line.lower() for line in output)
    assert all(TEST_PASSWORD not in line for line in output)


@pytest.mark.asyncio
async def test_invalid_email_and_username_fail_without_creation(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
) -> None:
    repository, factory = bootstrap_dependencies
    output: list[str] = []
    prompts = iter(["yes", "not-an-email", "valid-username"])
    secrets = iter([TEST_PASSWORD, TEST_PASSWORD])

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )
    assert result != 0
    assert repository.created is None

    output.clear()
    prompts = iter(["yes", "first@example.com", "x"])
    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )
    assert result != 0
    assert repository.created is None


@pytest.mark.asyncio
async def test_existing_password_policy_is_enforced(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
) -> None:
    repository, factory = bootstrap_dependencies
    prompts = iter(["yes", "first@example.com", "first-admin"])
    secrets = iter(["weak", "weak"])
    output: list[str] = []

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )

    assert result != 0
    assert repository.created is None
    assert any("policy" in line.lower() for line in output)


@pytest.mark.asyncio
async def test_duplicate_constraint_failure_is_safe_and_does_not_persist(
    bootstrap_dependencies: tuple[FakeAdminRepository, FakeSessionFactory],
) -> None:
    repository, factory = bootstrap_dependencies
    repository.create_error = IntegrityError("insert", {}, RuntimeError("duplicate"))
    prompts = iter(["yes", "first@example.com", "first-admin"])
    secrets = iter([TEST_PASSWORD, TEST_PASSWORD])
    output: list[str] = []

    result = await bootstrap_module.bootstrap_first_admin(
        session_factory=factory,
        prompt=lambda _: next(prompts),
        secret_prompt=lambda _: next(secrets),
        output=output.append,
    )

    assert result != 0
    assert repository.created is None
    assert any("validation" in line.lower() for line in output)
    assert "duplicate" not in " ".join(output).lower()
    assert factory.sessions[-1].rollback_count == 1


def test_repository_bootstrap_lock_is_postgresql_only_and_uses_table_lock() -> None:
    class LockSession:
        def __init__(self, dialect_name: str) -> None:
            self.dialect_name = dialect_name
            self.statements: list[str] = []

        def get_bind(self) -> SimpleNamespace:
            return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect_name))

        async def execute(self, statement: Any) -> None:
            self.statements.append(str(statement))

    postgres_session = LockSession("postgresql")
    repository = AdminRepository(postgres_session)  # type: ignore[arg-type]
    import asyncio

    asyncio.run(repository.acquire_first_admin_bootstrap_lock())
    assert postgres_session.statements == ["LOCK TABLE admins IN SHARE ROW EXCLUSIVE MODE"]

    sqlite_repository = AdminRepository(LockSession("sqlite"))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="requires PostgreSQL"):
        asyncio.run(sqlite_repository.acquire_first_admin_bootstrap_lock())


def test_import_does_not_trigger_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_bootstrap(*_: Any, **__: Any) -> None:
        raise AssertionError("import must not run the Admin bootstrap")

    monkeypatch.setattr(bootstrap_module, "_build_staff_service", unexpected_bootstrap)
    importlib.reload(bootstrap_module)


@pytest.mark.asyncio
async def test_fastapi_startup_does_not_run_first_admin_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_op() -> None:
        return None

    async def start_worker(_: Any) -> None:
        return None

    import app.jobs.virtual_account_retry_job as retry_job

    monkeypatch.setattr(app_main, "connect_redis", no_op)
    monkeypatch.setattr(app_main, "_verify_database_connection", no_op)
    monkeypatch.setattr(retry_job, "start_background_retry_worker", start_worker)

    def unexpected_bootstrap(*_: Any, **__: Any) -> None:
        raise AssertionError("FastAPI startup must not invoke first-Admin bootstrap")

    monkeypatch.setattr(bootstrap_module, "_build_staff_service", unexpected_bootstrap)
    app = app_main.create_app()
    await app.router.startup()