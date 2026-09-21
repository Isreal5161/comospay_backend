from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any
from uuid import UUID

from app.models.api_key import APIKey
from app.repositories.api_key_repository import APIKeyRepository
from app.utils.exceptions import ValidationException
from app.utils.token import generate_api_key


class ApiKeyService:
    """Service for admin API key creation and management."""

    def __init__(self, *, api_key_repository: APIKeyRepository, logger: logging.Logger | None = None) -> None:
        self.api_key_repository = api_key_repository
        self.logger = logger or logging.getLogger(__name__)

    async def create_api_key(self, **payload: Any) -> dict[str, Any]:
        name = payload.get("name")
        if not name:
            raise ValidationException(detail="API key name is required.", error_code="API_KEY_NAME_REQUIRED")

        key_value = generate_api_key(64)
        api_key = APIKey(
            key_id=str(uuid.uuid4()),
            name=name,
            description=payload.get("description"),
            owner_type=payload.get("owner_type") or "admin",
            owner_id=payload.get("owner_id"),
            environment=payload.get("environment") or "production",
            status=payload.get("status") or "active",
            is_active=payload.get("is_active") if payload.get("is_active") is not None else True,
            expires_at=payload.get("expires_at"),
            tags=payload.get("tags"),
            hashed_key=self._hash_key(key_value),
            prefix=key_value[:8],
        )
        created = await self.api_key_repository.create_api_key(api_key)
        return {
            "success": True,
            "data": {"api_key": self._serialize_api_key(created), "secret": key_value},
            "meta": {"source": "admin.api_keys"},
        }

    async def update_api_key(self, *, key_id: str, **payload: Any) -> dict[str, Any]:
        api_key = await self.api_key_repository.get_by_id(UUID(key_id))
        if api_key is None:
            raise ValidationException(detail="API key not found.", error_code="API_KEY_NOT_FOUND")

        updated = await self.api_key_repository.update_api_key(
            api_key,
            name=payload.get("name"),
            description=payload.get("description"),
            status=payload.get("status"),
            is_active=payload.get("is_active"),
            expires_at=payload.get("expires_at"),
            tags=payload.get("tags"),
        )
        return {"success": True, "data": self._serialize_api_key(updated), "meta": {"source": "admin.api_keys"}}

    async def revoke_api_key(self, *, key_id: str, **payload: Any) -> dict[str, Any]:
        api_key = await self.api_key_repository.get_by_id(UUID(key_id))
        if api_key is None:
            raise ValidationException(detail="API key not found.", error_code="API_KEY_NOT_FOUND")

        revoked = await self.api_key_repository.revoke_api_key(api_key, reason=payload.get("reason"))
        return {"success": True, "data": self._serialize_api_key(revoked), "meta": {"source": "admin.api_keys"}}

    def _hash_key(self, key_value: str) -> str:
        return hashlib.sha256(key_value.encode("utf-8")).hexdigest()

    def _serialize_api_key(self, api_key: APIKey) -> dict[str, Any]:
        return {
            "id": str(api_key.id),
            "key_id": api_key.key_id,
            "name": api_key.name,
            "description": api_key.description,
            "owner_type": api_key.owner_type,
            "owner_id": api_key.owner_id,
            "environment": api_key.environment,
            "status": api_key.status,
            "is_active": api_key.is_active,
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            "last_used_at": api_key.last_used_at.isoformat() if api_key.last_used_at else None,
            "revoked_at": api_key.revoked_at.isoformat() if api_key.revoked_at else None,
            "revoked_reason": api_key.revoked_reason,
            "prefix": api_key.prefix,
            "tags": api_key.tags,
            "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
            "updated_at": api_key.updated_at.isoformat() if api_key.updated_at else None,
        }
