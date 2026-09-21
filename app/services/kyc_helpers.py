from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.utils.exceptions import ValidationException

if TYPE_CHECKING:
    from app.models.kyc import KYC
    from app.models.user import User
    from app.repositories.kyc_repository import KYCRepository
    from app.repositories.user_repository import UserRepository


async def get_user_or_raise(
    *,
    user_repository: UserRepository,
    user_id: UUID,
    detail: str = "User not found.",
    error_code: str = "USER_NOT_FOUND",
) -> User:
    """Fetch a user by ID and raise a validation error when absent."""
    user = await user_repository.get_by_id(user_id)
    if user is None:
        raise ValidationException(detail=detail, error_code=error_code)
    return user


async def get_user_kyc_records(
    *,
    kyc_repository: KYCRepository,
    user_id: UUID,
    page: int = 1,
    page_size: int = 20,
) -> list[KYC]:
    """Retrieve paginated KYC records for a user."""
    records, _ = await kyc_repository.get_user_kyc(user_id=user_id, page=page, page_size=page_size)
    return records


async def get_active_submission(
    *,
    kyc_repository: KYCRepository,
    user_id: UUID,
    page: int = 1,
    page_size: int = 20,
) -> KYC | None:
    """Return the first active pending/reviewing submission for a user."""
    records = await get_user_kyc_records(kyc_repository=kyc_repository, user_id=user_id, page=page, page_size=page_size)
    for record in records:
        if record.is_active and record.verification_status in {"pending", "reviewing"}:
            return record
    return None


async def get_latest_submission(
    *,
    kyc_repository: KYCRepository,
    user_id: UUID,
    page: int = 1,
    page_size: int = 20,
    prefer_active: bool = False,
) -> KYC | None:
    """Return the latest submission, optionally preferring an active record."""
    records = await get_user_kyc_records(kyc_repository=kyc_repository, user_id=user_id, page=page, page_size=page_size)
    if not records:
        return None
    if prefer_active:
        active = next((item for item in records if bool(getattr(item, "is_active", False))), None)
        if active is not None:
            return active
    return records[0]