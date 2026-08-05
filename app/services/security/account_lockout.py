from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

for candidate_root in {
    Path(__file__).resolve().parents[3],
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend",
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend" / "app",
}:
    if candidate_root.exists() and str(candidate_root) not in sys.path:
        sys.path.insert(0, str(candidate_root))

try:
    from app.config.settings import settings as default_settings
except ImportError:  # pragma: no cover - compatibility fallback
    default_settings = None

try:
    from app.models.user import User
except ImportError:  # pragma: no cover - compatibility fallback
    User = Any  # type: ignore[misc,assignment]

try:
    from app.repositories.user_repository import UserRepository
except ImportError:  # pragma: no cover - compatibility fallback
    UserRepository = Any  # type: ignore[misc,assignment]

try:
    from app.utils.exceptions import DatabaseException, ValidationException
except ImportError:  # pragma: no cover - compatibility fallback
    DatabaseException = ValidationException = Any  # type: ignore[misc,assignment]


class AccountLockoutService:
    """Enterprise account lockout orchestration for security enforcement.

    This service is intentionally limited to lockout lifecycle operations and
    does not implement authentication or controller logic. It relies on the
    repository and settings layers already used by the backend.
    """

    def __init__(
        self,
        *,
        user_repository: UserRepository | None = None,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client

    async def lock_account(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        duration_minutes: int | None = None,
        permanent: bool | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Lock a user account temporarily or permanently."""
        user = await self._get_user(user_id)
        now = self._utc_now()

        if self._is_account_locked(user, now):
            return self._build_state_payload(
                user=user,
                locked=True,
                action="already_locked",
                reason=(reason or self._get_lock_reason(user)) or "existing_lock",
                lock_type=self._resolve_lock_type(user),
                lock_until=user.account_locked_until,
            )

        lock_type = "permanent" if permanent else "temporary"
        lock_until = None if permanent else self._calculate_lock_until(now, duration_minutes=duration_minutes)
        await self._persist_lock_state(
            user=user,
            lock_until=lock_until,
            reason=reason,
            lock_type=lock_type,
            source=payload.get("source", "manual"),
        )

        self._log_event(
            "account_locked",
            user_id=user.id,
            lock_type=lock_type,
            reason=reason,
            lock_until=lock_until.isoformat() if lock_until else None,
        )
        return self._build_state_payload(
            user=user,
            locked=True,
            action="locked",
            reason=reason,
            lock_type=lock_type,
            lock_until=lock_until,
        )

    async def unlock_account(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        by_admin: bool | None = None,
        password_reset: bool | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Unlock a user account and reset relevant security state."""
        user = await self._get_user(user_id)
        now = self._utc_now()

        if not self._is_account_locked(user, now):
            await self._clear_lock_state(
                user=user,
                reason=reason or "unlock_request",
                source=payload.get("source", "manual"),
                reset_attempts=payload.get("reset_attempts", True),
            )
            return self._build_state_payload(
                user=user,
                locked=False,
                action="already_unlocked",
                reason=reason,
                lock_type=None,
                lock_until=None,
            )

        await self._clear_lock_state(
            user=user,
            reason=reason or ("administrator_unlock" if by_admin else "manual_unlock" if password_reset is None else "password_reset_unlock"),
            source=payload.get("source", "manual"),
            reset_attempts=payload.get("reset_attempts", True),
        )

        self._log_event(
            "account_unlocked",
            user_id=user.id,
            reason=reason,
            by_admin=bool(by_admin),
            password_reset=bool(password_reset),
        )
        return self._build_state_payload(
            user=user,
            locked=False,
            action="unlocked",
            reason=reason,
            lock_type=None,
            lock_until=None,
        )

    async def temporary_lock(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        duration_minutes: int | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Temporarily lock a user account for a configured or supplied duration."""
        return await self.lock_account(
            user_id=user_id,
            reason=reason,
            duration_minutes=duration_minutes,
            permanent=False,
            **payload,
        )

    async def permanent_lock(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Permanently lock a user account until manually unlocked."""
        return await self.lock_account(
            user_id=user_id,
            reason=reason,
            duration_minutes=None,
            permanent=True,
            **payload,
        )

    async def is_account_locked(self, *, user_id: str | UUID | None, **payload: Any) -> dict[str, Any]:
        """Return lock state for a user while applying automatic unlock when expired."""
        user = await self._get_user(user_id)
        now = self._utc_now()
        if self._is_expired_lock(user, now) and self._auto_unlock_enabled():
            await self.unlock_account(user_id=user.id, reason="automatic_unlock", by_admin=False, password_reset=False, **payload)
            user = await self._get_user(user.id)
            now = self._utc_now()

        return self._build_state_payload(
            user=user,
            locked=self._is_account_locked(user, now),
            action="status_check",
            reason=self._get_lock_reason(user),
            lock_type=self._resolve_lock_type(user),
            lock_until=user.account_locked_until,
        )

    async def get_lock_information(self, *, user_id: str | UUID | None, **payload: Any) -> dict[str, Any]:
        """Retrieve the current lock information for a user."""
        return await self.is_account_locked(user_id=user_id, **payload)

    async def increment_failed_attempts(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Record a failed authentication event and escalate locking if required."""
        user = await self._get_user(user_id)
        now = self._utc_now()

        if self._is_account_locked(user, now):
            return self._build_state_payload(
                user=user,
                locked=True,
                action="already_locked",
                reason=reason or self._get_lock_reason(user),
                lock_type=self._resolve_lock_type(user),
                lock_until=user.account_locked_until,
            )

        failed_attempts = int(user.failed_login_attempts or 0) + 1
        user.failed_login_attempts = failed_attempts
        await self._persist_user_state(
            user=user,
            failed_login_attempts=failed_attempts,
            account_locked_until=None,
            status=user.status,
            lock_reason=None,
            lock_type="none",
            source=payload.get("source", "failed_login"),
        )

        if self.should_lock_account(failed_attempts=failed_attempts):
            lock_reason = reason or "max_failed_attempts_exceeded"
            if self._is_permanent_lock_threshold(failed_attempts):
                return await self.permanent_lock(user_id=user.id, reason=lock_reason, source="failed_login")
            return await self.temporary_lock(
                user_id=user.id,
                reason=lock_reason,
                duration_minutes=self.calculate_lock_duration(failed_attempts=failed_attempts),
                source="failed_login",
            )

        self._log_event(
            "failed_login_attempt_recorded",
            user_id=user.id,
            failed_attempts=failed_attempts,
            threshold=self._max_attempts_threshold(),
        )
        return self._build_state_payload(
            user=user,
            locked=False,
            action="attempt_recorded",
            reason=reason,
            lock_type=None,
            lock_until=None,
            failed_attempts=failed_attempts,
        )

    async def reset_failed_attempts(
        self,
        *,
        user_id: str | UUID | None,
        reason: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Reset failed-login counters after a successful login or administrative action."""
        user = await self._get_user(user_id)
        user.failed_login_attempts = 0
        await self._persist_user_state(
            user=user,
            failed_login_attempts=0,
            account_locked_until=None,
            status="active",
            lock_reason=None,
            lock_type="none",
            source=payload.get("source", "reset"),
        )
        self._log_event("failed_attempts_reset", user_id=user.id, reason=reason)
        return self._build_state_payload(user=user, locked=False, action="attempts_reset", reason=reason, lock_type=None, lock_until=None)

    def calculate_lock_duration(self, *, failed_attempts: int | None = None, **payload: Any) -> int:
        """Calculate the temporary lock duration using configured settings."""
        failed_attempts_value = int(failed_attempts or 0)
        base_duration = self._get_setting("account_lock_duration_minutes", 15)
        if not self._progressive_lock_enabled():
            return int(base_duration)

        factor = self._get_setting("account_lock_progressive_factor", 2)
        duration = int(base_duration * (factor ** max(0, failed_attempts_value - 1)))
        max_duration = self._get_setting("account_lock_max_duration_minutes")
        if max_duration is not None:
            duration = min(duration, int(max_duration))
        return max(int(duration), int(base_duration))

    def should_lock_account(self, *, failed_attempts: int | None = None, **payload: Any) -> bool:
        """Determine whether the supplied failed-attempt count exceeds the active threshold."""
        failed_attempts_value = int(failed_attempts or 0)
        threshold = self._max_attempts_threshold()
        permanent_threshold = self._get_setting("permanent_lock_threshold")
        if permanent_threshold is not None and failed_attempts_value >= int(permanent_threshold):
            return True
        return failed_attempts_value >= threshold

    async def _persist_lock_state(
        self,
        *,
        user: User,
        lock_until: datetime | None,
        reason: str | None,
        lock_type: str,
        source: str,
    ) -> None:
        user.account_locked_until = lock_until
        user.failed_login_attempts = int(user.failed_login_attempts or 0)
        user.status = "locked" if lock_until or lock_type == "permanent" else user.status
        await self._persist_user_state(
            user=user,
            failed_login_attempts=user.failed_login_attempts,
            account_locked_until=lock_until,
            status=user.status,
            lock_reason=reason,
            lock_type=lock_type,
            source=source,
        )

    async def _clear_lock_state(
        self,
        *,
        user: User,
        reason: str | None,
        source: str,
        reset_attempts: bool,
    ) -> None:
        user.account_locked_until = None
        if reset_attempts:
            user.failed_login_attempts = 0
        user.status = "active"
        await self._persist_user_state(
            user=user,
            failed_login_attempts=user.failed_login_attempts,
            account_locked_until=None,
            status=user.status,
            lock_reason=None,
            lock_type="none",
            source=source,
        )

    async def _persist_user_state(
        self,
        *,
        user: User,
        failed_login_attempts: int,
        account_locked_until: datetime | None,
        status: str,
        lock_reason: str | None,
        lock_type: str,
        source: str,
    ) -> None:
        if self.user_repository is None:
            raise DatabaseException("User repository is not configured for account lockout operations.")

        metadata = self._read_metadata(user)
        metadata["security_lock_state"] = {
            "lock_reason": lock_reason,
            "lock_type": lock_type,
            "source": source,
            "updated_at": self._utc_now().isoformat(),
        }
        if lock_type == "none":
            metadata.pop("security_lock_state", None)
            metadata.pop("lock_reason", None)
            metadata.pop("lock_type", None)
            metadata.pop("lock_source", None)
            metadata.pop("lock_until", None)
        else:
            metadata["lock_reason"] = lock_reason
            metadata["lock_type"] = lock_type
            metadata["lock_source"] = source
            metadata["lock_until"] = account_locked_until.isoformat() if account_locked_until else None

        user.metadata_payload = json.dumps(metadata) if metadata else None
        await self.user_repository.update_user(
            user,
            failed_login_attempts=failed_login_attempts,
            account_locked_until=account_locked_until,
            status=status,
            metadata_payload=user.metadata_payload,
        )
        await self._sync_redis(user=user, lock_until=account_locked_until, lock_reason=lock_reason, lock_type=lock_type, source=source)

    async def _get_user(self, user_id: str | UUID | None) -> User:
        if user_id is None:
            raise ValidationException("User identifier is required.")

        user_identifier = self._normalize_user_id(user_id)
        if self.user_repository is None:
            raise DatabaseException("User repository is not configured for account lockout operations.")

        user = await self.user_repository.get_by_id(user_identifier)
        if user is None:
            raise ValidationException("User not found.")
        return user

    async def _sync_redis(
        self,
        *,
        user: User,
        lock_until: datetime | None,
        lock_reason: str | None,
        lock_type: str,
        source: str,
    ) -> None:
        try:
            redis_client = self.redis_client
            if redis_client is None:
                try:
                    from app.config.redis import get_redis as redis_getter
                except ImportError:  # pragma: no cover - compatibility fallback
                    try:
                        from app.config.redis import get_redis as redis_getter
                    except ImportError:  # pragma: no cover - compatibility fallback
                        return
                redis_client = await redis_getter()
            if redis_client is None:
                return
            payload = {
                "user_id": str(user.id),
                "locked": bool(lock_until or lock_type == "permanent"),
                "reason": lock_reason,
                "lock_type": lock_type,
                "source": source,
                "lock_until": lock_until.isoformat() if lock_until else None,
            }
            await redis_client.setex(f"account_lock:{user.id}", max(60, self._get_setting("redis_cache_ttl", 300)), json.dumps(payload))
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.logger.warning("account_lockout_redis_sync_failed", extra={"event_type": "security", "component": "AccountLockoutService", "error": str(exc)})

    def _build_state_payload(
        self,
        *,
        user: User,
        locked: bool,
        action: str,
        reason: str | None,
        lock_type: str | None,
        lock_until: datetime | None,
        failed_attempts: int | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "locked": locked,
            "action": action,
            "user_id": str(user.id),
            "reason": reason,
            "lock_type": lock_type,
            "lock_until": lock_until.isoformat() if lock_until else None,
            "failed_attempts": failed_attempts if failed_attempts is not None else int(user.failed_login_attempts or 0),
            "status": getattr(user, "status", None),
        }

    def _is_account_locked(self, user: User, now: datetime | None = None) -> bool:
        current_time = now or self._utc_now()
        if user.account_locked_until is None:
            return False
        if user.account_locked_until <= current_time:
            return False
        return True

    def _is_expired_lock(self, user: User, now: datetime | None = None) -> bool:
        current_time = now or self._utc_now()
        if user.account_locked_until is None:
            return False
        return user.account_locked_until <= current_time

    def _calculate_lock_until(self, now: datetime, *, duration_minutes: int | None = None) -> datetime:
        duration = duration_minutes if duration_minutes is not None else self.calculate_lock_duration(failed_attempts=int(self._get_failed_attempts()))
        return now + timedelta(minutes=max(int(duration), 1))

    def _get_failed_attempts(self) -> int:
        return 0

    def _resolve_lock_type(self, user: User) -> str | None:
        if user.account_locked_until is None:
            return None
        if self._is_permanent_lock_threshold(int(user.failed_login_attempts or 0)):
            return "permanent"
        return "temporary"

    def _max_attempts_threshold(self) -> int:
        return int(self._get_setting("max_login_attempts", 5))

    def _progressive_lock_enabled(self) -> bool:
        return bool(self._get_setting("account_lock_progressive_enabled", False))

    def _auto_unlock_enabled(self) -> bool:
        return bool(self._get_setting("account_lock_auto_unlock", True))

    def _get_lock_reason(self, user: User) -> str | None:
        metadata = self._read_metadata(user)
        return metadata.get("lock_reason") or metadata.get("security_lock_state", {}).get("lock_reason")

    def _read_metadata(self, user: User) -> dict[str, Any]:
        if not getattr(user, "metadata_payload", None):
            return {}
        try:
            payload = json.loads(user.metadata_payload)
            if isinstance(payload, dict):
                return payload
        except (TypeError, ValueError):
            return {}
        return {}

    def _normalize_user_id(self, user_id: str | UUID) -> UUID:
        if isinstance(user_id, UUID):
            return user_id
        try:
            return UUID(str(user_id))
        except (TypeError, ValueError) as exc:
            raise ValidationException("User identifier is invalid.") from exc

    def _get_setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _is_permanent_lock_threshold(self, failed_attempts: int) -> bool:
        permanent_threshold = self._get_setting("permanent_lock_threshold")
        if permanent_threshold is None:
            return False
        return int(failed_attempts) >= int(permanent_threshold)

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _log_event(self, event_name: str, **context: Any) -> None:
        self.logger.info(
            event_name,
            extra={"event_type": "security", "component": "AccountLockoutService", **context},
        )


__all__ = ["AccountLockoutService"]
