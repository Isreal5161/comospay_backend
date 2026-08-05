from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kyc import KYC
from app.models.user import User
from app.repositories.kyc_repository import KYCRepository
from app.repositories.user_repository import UserRepository
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException


class KYCService:
    """Coordinate KYC submission and review workflows for authenticated users."""

    supported_document_types = {"bvn", "nin", "passport", "drivers_license", "international_passport"}
    supported_levels = {"basic", "standard", "premium"}
    supported_statuses = {"pending", "reviewing", "approved", "rejected", "expired"}

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        kyc_repository: KYCRepository,
        provider_service: Any | None = None,
        notification_service: Any | None = None,
        audit_service: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.kyc_repository = kyc_repository
        self.provider_service = provider_service
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def submit_kyc(
        self,
        *,
        user_id: UUID,
        document_type: str,
        document_reference: str | None = None,
        verification_level: str = "basic",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a new KYC submission for a user after validation."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)
        self._validate_required_fields(document_type=document_type, verification_level=verification_level)
        self._validate_document_type(document_type)
        self._validate_verification_level(verification_level)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        existing_active = await self._get_active_submission(user_id=user_id)
        if existing_active:
            raise ValidationException("An active KYC submission already exists.")

        normalized_reference = self._sanitize_reference(document_reference)
        normalized_metadata = self._sanitize_metadata(metadata_payload)

        try:
            async with self._session_scope():
                kyc = KYC(
                    user_id=user.id,
                    verification_status="pending",
                    verification_level=verification_level,
                    document_type=document_type,
                    document_reference=normalized_reference,
                    document_verification_status="pending",
                    submitted_at=datetime.now(timezone.utc),
                    reviewed_at=None,
                    reviewed_by=None,
                    approved_at=None,
                    rejected_at=None,
                    rejection_reason=None,
                    compliance_notes=None,
                    is_active=True,
                    is_verified=False,
                    metadata_payload=normalized_metadata,
                )
                await self.kyc_repository.create_kyc(kyc)
                await self._log_event("kyc_submitted", user_id=user.id)
                return self._serialize_kyc(kyc)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("KYC submission failed.") from exc

    async def get_kyc_status(self, *, user_id: UUID) -> dict[str, Any]:
        """Return the latest KYC status for the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        kyc = await self._get_latest_submission(user_id=user_id)
        if not kyc:
            return {"status": "not_started", "verification_level": "basic", "message": "No KYC submission found."}
        return {
            "status": kyc.verification_status,
            "verification_level": kyc.verification_level,
            "is_verified": kyc.is_verified,
            "is_active": kyc.is_active,
            "document_verification_status": kyc.document_verification_status,
        }

    async def get_kyc_details(self, *, user_id: UUID) -> dict[str, Any]:
        """Return the latest KYC submission details without exposing sensitive identifiers."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        kyc = await self._get_latest_submission(user_id=user_id)
        if not kyc:
            return {"message": "No KYC submission found."}
        return self._serialize_kyc(kyc)

    async def update_kyc(
        self,
        *,
        user_id: UUID,
        document_type: str | None = None,
        document_reference: str | None = None,
        verification_level: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Update a pending KYC submission when business rules allow it."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        kyc = await self._get_latest_submission(user_id=user_id)
        if not kyc:
            raise ValidationException("No KYC submission found.")
        if kyc.verification_status in {"approved", "reviewing"}:
            raise ValidationException("Approved or reviewing KYC records cannot be modified.")

        if document_type is not None:
            self._validate_document_type(document_type)
            kyc.document_type = document_type
        if document_reference is not None:
            kyc.document_reference = self._sanitize_reference(document_reference)
        if verification_level is not None:
            self._validate_verification_level(verification_level)
            kyc.verification_level = verification_level
        if metadata_payload is not None:
            kyc.metadata_payload = self._sanitize_metadata(metadata_payload)

        try:
            async with self._session_scope():
                await self.kyc_repository.update_kyc(
                    kyc,
                    document_type=kyc.document_type,
                    document_reference=kyc.document_reference,
                    verification_level=kyc.verification_level,
                    metadata_payload=kyc.metadata_payload,
                )
                await self._log_event("kyc_updated", user_id=user.id)
                return self._serialize_kyc(kyc)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("KYC update failed.") from exc

    async def resubmit_kyc(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        """Create a fresh KYC submission after rejection or expiry."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        existing_active = await self._get_active_submission(user_id=user_id)
        if existing_active:
            raise ValidationException("An active KYC submission already exists.")

        prior = await self._get_latest_submission(user_id=user_id)
        if prior and prior.verification_status not in {"rejected", "expired"}:
            raise ValidationException("KYC can only be resubmitted after rejection or expiry.")

        return await self.submit_kyc(
            user_id=user_id,
            document_type=payload.get("document_type"),
            document_reference=payload.get("document_reference"),
            verification_level=payload.get("verification_level", "basic"),
            metadata_payload=payload.get("metadata_payload"),
        )

    async def can_perform_verified_operations(self, *, user_id: UUID, required_level: str = "standard") -> bool:
        """Return whether the user has a sufficient KYC level for restricted operations."""
        self._require_repository(self.user_repository)
        self._require_repository(self.kyc_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        kyc = await self._get_latest_submission(user_id=user_id)
        if not kyc:
            return False
        if kyc.verification_status != "approved" or not kyc.is_verified:
            return False
        return self._level_rank(kyc.verification_level) >= self._level_rank(required_level)

    def _validate_required_fields(self, **values: Any) -> None:
        for field_name, value in values.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationException(f"{field_name} is required.")

    def _validate_document_type(self, document_type: str) -> None:
        if document_type not in self.supported_document_types:
            raise ValidationException("Unsupported document type.")

    def _validate_verification_level(self, verification_level: str) -> None:
        if verification_level not in self.supported_levels:
            raise ValidationException("Unsupported verification level.")

    def _sanitize_reference(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationException("document_reference must be a string.")
        sanitized = value.strip()
        if len(sanitized) > 500:
            raise ValidationException("document_reference is too long.")
        return sanitized or None

    def _sanitize_metadata(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationException("metadata_payload must be a string.")
        sanitized = value.strip()
        if len(sanitized) > 2000:
            raise ValidationException("metadata_payload is too long.")
        return sanitized or None

    def _serialize_kyc(self, kyc: KYC) -> dict[str, Any]:
        return {
            "id": str(kyc.id),
            "user_id": str(kyc.user_id),
            "verification_status": kyc.verification_status,
            "verification_level": kyc.verification_level,
            "document_type": kyc.document_type,
            "document_reference": self._mask_sensitive_value(kyc.document_reference),
            "document_verification_status": kyc.document_verification_status,
            "submitted_at": kyc.submitted_at.isoformat() if kyc.submitted_at else None,
            "reviewed_at": kyc.reviewed_at.isoformat() if kyc.reviewed_at else None,
            "approved_at": kyc.approved_at.isoformat() if kyc.approved_at else None,
            "rejected_at": kyc.rejected_at.isoformat() if kyc.rejected_at else None,
            "rejection_reason": kyc.rejection_reason,
            "compliance_notes": kyc.compliance_notes,
            "is_active": kyc.is_active,
            "is_verified": kyc.is_verified,
            "created_at": kyc.created_at.isoformat() if kyc.created_at else None,
        }

    def _mask_sensitive_value(self, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"

    async def _get_active_submission(self, *, user_id: UUID) -> KYC | None:
        if self.kyc_repository is not None and hasattr(self.kyc_repository, "get_user_kyc"):
            records, _ = await self.kyc_repository.get_user_kyc(user_id=user_id, page=1, page_size=20)
            for record in records:
                if record.is_active and record.verification_status in {"pending", "reviewing"}:
                    return record
        return None

    async def _get_latest_submission(self, *, user_id: UUID) -> KYC | None:
        if self.kyc_repository is not None and hasattr(self.kyc_repository, "get_user_kyc"):
            records, _ = await self.kyc_repository.get_user_kyc(user_id=user_id, page=1, page_size=20)
            return records[0] if records else None
        return None

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "kyc_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if callable(getattr(self, "audit_service", None)):
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for KYCService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()

    def _level_rank(self, level: str) -> int:
        mapping = {"basic": 1, "standard": 2, "premium": 3}
        return mapping.get(level.lower(), 0)


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
