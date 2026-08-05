from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.cloudinary import build_image_url, delete_image, upload_image
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException


class CloudinaryProfileAdapter:
    """Small adapter for injected profile image provider interactions."""

    async def upload(self, *, file_path: str, public_id: str | None = None, folder: str | None = None) -> dict[str, Any]:
        return upload_image(file_path=file_path, public_id=public_id, folder=folder)

    async def delete(self, *, public_id: str) -> dict[str, Any]:
        return delete_image(public_id)

    def build_url(self, *, public_id: str, version: int | None = None) -> str:
        return build_image_url(public_id, version=version)


class ProfileService:
    """Manage authenticated user profile operations."""

    protected_fields = {"id", "email", "password_hash", "transaction_pin_reference", "otp", "status", "created_at", "updated_at"}

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        cloudinary_service: CloudinaryProfileAdapter | None = None,
        audit_service: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.cloudinary_service = cloudinary_service or CloudinaryProfileAdapter()
        self.audit_service = audit_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def get_profile(self, *, user_id: UUID) -> dict[str, Any]:
        """Return a sanitized profile payload for an authenticated user."""
        self._require_repository(self.user_repository)
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")
        return self._serialize_profile(user)

    async def update_profile(self, *, user_id: UUID, profile_data: dict[str, Any]) -> dict[str, Any]:
        """Update editable profile fields for the authenticated user."""
        self._require_repository(self.user_repository)
        if not profile_data:
            raise ValidationException("No profile fields provided.")

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        allowed_fields = {
            "first_name",
            "last_name",
            "phone",
            "username",
            "locale",
            "timezone_name",
            "metadata_payload",
        }

        invalid_fields = sorted(set(profile_data.keys()) - allowed_fields)
        if invalid_fields:
            raise ValidationException(f"Unsupported profile fields: {', '.join(invalid_fields)}")

        sanitized_payload: dict[str, Any] = {}
        for field_name, value in profile_data.items():
            sanitized_value = self._sanitize_field(field_name, value)
            sanitized_payload[field_name] = sanitized_value

        try:
            async with self._session_scope():
                for field_name, value in sanitized_payload.items():
                    setattr(user, field_name, value)
                await self.user_repository.update_user(user, **sanitized_payload)
                await self._log_event("profile_updated", user_id=user.id)
                return self._serialize_profile(user)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Profile update failed.") from exc

    async def upload_profile_photo(self, *, user_id: UUID, file_path: str, public_id: str | None = None) -> dict[str, Any]:
        """Upload a profile image and persist the resulting URL and public ID."""
        self._require_repository(self.user_repository)
        if not file_path:
            raise ValidationException("A file path is required.")

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        self._validate_image_path(file_path)

        try:
            provider_response = await self.cloudinary_service.upload(
                file_path=file_path,
                public_id=public_id,
                folder="profiles",
            )
        except Exception as exc:
            raise ProviderException("Profile image upload failed.") from exc

        secure_url = provider_response.get("secure_url") or provider_response.get("url")
        if not secure_url:
            raise ProviderException("Profile image upload failed.")

        try:
            async with self._session_scope():
                user.profile_image_url = secure_url
                await self.user_repository.update_user(user, profile_image_url=secure_url)
                await self._log_event("profile_photo_uploaded", user_id=user.id)
                return self._serialize_profile(user)
        except Exception as exc:
            raise DatabaseException("Profile image update failed.") from exc

    async def remove_profile_photo(self, *, user_id: UUID) -> dict[str, Any]:
        """Remove the existing profile image from the provider and clear the local profile field."""
        self._require_repository(self.user_repository)
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")
        if not user.profile_image_url:
            raise ValidationException("No profile photo is currently set.")

        try:
            public_id = self._extract_public_id(user.profile_image_url)
            if public_id:
                await self.cloudinary_service.delete(public_id=public_id)
        except Exception as exc:
            raise ProviderException("Profile image removal failed.") from exc

        try:
            async with self._session_scope():
                user.profile_image_url = None
                await self.user_repository.update_user(user, profile_image_url=None)
                await self._log_event("profile_photo_removed", user_id=user.id)
                return self._serialize_profile(user)
        except Exception as exc:
            raise DatabaseException("Profile image removal failed.") from exc

    def _sanitize_field(self, field_name: str, value: Any) -> Any:
        if field_name in {"first_name", "last_name", "username", "locale", "timezone_name"}:
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValidationException(f"{field_name} must be a string.")
            sanitized = " ".join(value.strip().split()) if field_name in {"first_name", "last_name"} else value.strip()
            if field_name in {"first_name", "last_name"} and len(sanitized) > 100:
                raise ValidationException(f"{field_name} is too long.")
            if field_name == "username" and len(sanitized) > 100:
                raise ValidationException("username is too long.")
            if field_name == "locale" and len(sanitized) > 20:
                raise ValidationException("locale is too long.")
            if field_name == "timezone_name" and len(sanitized) > 100:
                raise ValidationException("timezone_name is too long.")
            return sanitized or None

        if field_name == "phone":
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValidationException("phone must be a string.")
            sanitized = value.strip()
            if not sanitized:
                return None
            if len(sanitized) > 20:
                raise ValidationException("phone is too long.")
            return sanitized

        if field_name == "metadata_payload":
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValidationException("metadata_payload must be a string.")
            sanitized = value.strip()
            if len(sanitized) > 1000:
                raise ValidationException("metadata_payload is too long.")
            return sanitized or None

        raise ValidationException(f"Unsupported profile field: {field_name}")

    def _serialize_profile(self, user: User) -> dict[str, Any]:
        return {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "username": user.username,
            "profile_image_url": user.profile_image_url,
            "status": user.status,
            "is_active": user.is_active,
            "email_verified": user.email_verified,
            "phone_verified": user.phone_verified,
            "locale": user.locale,
            "timezone_name": user.timezone_name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

    def _validate_image_path(self, file_path: str) -> None:
        if not file_path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            raise ValidationException("Only JPG, JPEG, PNG, and WEBP images are supported.")

    def _extract_public_id(self, image_url: str) -> str | None:
        if not image_url:
            return None
        try:
            return image_url.split("/upload/")[-1].split("/")[-1].split(".")[0]
        except Exception:
            return None

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "profile_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if callable(getattr(self, "audit_service", None)):
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("UserRepository is required for ProfileService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
