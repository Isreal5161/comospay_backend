from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from getpass import getpass
from pathlib import Path
import sys
from typing import Any

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.admin import Admin
from app.repositories.admin_repository import AdminRepository
from app.schemas.admin_schema import AdminCreate
from app.services.admin.staff import FirstAdminAlreadyExists, StaffAdministrationService
from app.services.auth.password_service import (
    InvalidPasswordException,
    PasswordPolicyViolationException,
    PasswordService,
    WeakPasswordException,
)
from app.utils.exceptions import ValidationException


logger = logging.getLogger(__name__)


def _build_staff_service(session: Any) -> StaffAdministrationService:
    return StaffAdministrationService(
        admin_repository=AdminRepository(session=session),
        password_service=PasswordService(),
        session=session,
    )


async def bootstrap_first_admin(
    *,
    session_factory: Any | None = None,
    prompt: Callable[[str], str] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
    output: Callable[[str], None] | None = None,
) -> int:
    """Explicitly create one first Admin; this function is never invoked at import/startup."""
    prompt = prompt or input
    secret_prompt = secret_prompt or getpass
    output = output or print

    from app.config.settings import settings

    output("WARNING: This operation creates the FIRST SUPER ADMIN for the currently configured database.")
    output(f"Environment: {settings.app_env}")
    if settings.app_env == "production":
        output("WARNING: THIS IS A PRODUCTION DATABASE OPERATION.")

    try:
        confirmation = prompt("Continue? [yes/no]: ")
    except (EOFError, OSError) as exc:
        logger.error("Admin bootstrap confirmation failed (%s).", type(exc).__name__)
        output("Bootstrap cancelled; no Admin was created.")
        return 1
    if confirmation != "yes":
        output("Bootstrap cancelled; no Admin was created.")
        return 1

    if session_factory is None:
        from app.config.database import AsyncSessionFactory

        session_factory = AsyncSessionFactory

    try:
        async with session_factory() as session:
            async with session.begin():
                if await _build_staff_service(session).has_admin_accounts():
                    output("Bootstrap refused: an Admin account already exists.")
                    return 1
    except Exception as exc:
        logger.error("Admin bootstrap preflight failed (%s).", type(exc).__name__)
        output("Bootstrap failed while checking Admin records.")
        return 1

    try:
        email = prompt("Admin email: ").strip()
        username = prompt("Admin username: ").strip()
        AdminCreate(
            email=email,
            username=username,
            password="TemporarySeedPassword123!",
            role="super_admin",
        )
    except (EOFError, OSError) as exc:
        logger.error("Admin bootstrap input failed (%s).", type(exc).__name__)
        output("Bootstrap cancelled; no Admin was created.")
        return 1
    except ValidationError:
        output("Invalid Admin email or username.")
        return 1

    try:
        password = secret_prompt("Admin password: ")
        confirmation = secret_prompt("Confirm Admin password: ")
    except (EOFError, OSError) as exc:
        logger.error("Admin bootstrap input failed (%s).", type(exc).__name__)
        output("Bootstrap cancelled; no Admin was created.")
        return 1

    try:
        async with session_factory() as session:
            admin = await _build_staff_service(session).bootstrap_first_admin(
                email=email,
                username=username,
                password=password,
                password_confirmation=confirmation,
            )
    except FirstAdminAlreadyExists:
        output("Bootstrap refused: an Admin account already exists.")
        return 1
    except InvalidPasswordException:
        output("Password confirmation is invalid or does not match.")
        return 1
    except (PasswordPolicyViolationException, WeakPasswordException):
        output("Password does not meet the configured policy.")
        return 1
    except ValidationException:
        output("Admin bootstrap validation failed.")
        return 1
    except Exception as exc:
        logger.error("Admin bootstrap failed (%s).", type(exc).__name__)
        output("Bootstrap failed; no Admin was created.")
        return 1

    _print_success(admin, output)
    return 0


def _print_success(admin: Admin, output: Callable[[str], None]) -> None:
    output("First Admin created successfully.")
    output(f"Email: {admin.email}")
    output(f"Role: {admin.role}")
    output(f"Status: {admin.status}")
    output(f"MFA enabled: {str(admin.mfa_enabled).lower()}")


async def main() -> int:
    return await bootstrap_first_admin()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
