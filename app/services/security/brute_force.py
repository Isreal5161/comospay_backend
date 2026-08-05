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


class BruteForceService:
    """Enterprise brute-force detection and mitigation service.

    The service tracks suspicious authentication activity using Redis-backed
    counters when available and falls back to an in-memory store for local
    execution. It is intentionally limited to attack-detection logic and
    delegates account-enforcement to the account lockout service when needed.
    """

    _COUNTER_PREFIX = "bruteforce:attempts"
    _ATTACK_PREFIX = "bruteforce:attack"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        user_repository: UserRepository | None = None,
        account_lockout_service: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.user_repository = user_repository
        self.account_lockout_service = account_lockout_service
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def record_failed_attempt(
        self,
        *,
        user_id: str | UUID | None = None,
        username: str | None = None,
        email: str | None = None,
        ip_address: str | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        session_id: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Record a failed authentication event and evaluate whether the activity should be blocked."""
        identifiers = self._collect_identifiers(
            user_id=user_id,
            username=username,
            email=email,
            ip_address=ip_address,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            session_id=session_id,
        )
        if not identifiers:
            raise ValidationException("At least one identifier is required to track brute-force activity.")

        if user_id is not None:
            await self._require_existing_user(user_id)

        now = self._utc_now()
        window_seconds = self._get_window_seconds()
        key_states: list[dict[str, Any]] = []
        for identifier_type, identifier_value in identifiers.items():
            if not identifier_value:
                continue
            key = self._counter_key(identifier_type, identifier_value)
            state = await self._load_state(key)
            state.setdefault("identifier_type", identifier_type)
            state.setdefault("identifier_value", str(identifier_value))
            state["count"] = int(state.get("count", 0)) + 1
            state["window_seconds"] = window_seconds
            state["updated_at"] = now.isoformat()
            state["first_seen_at"] = state.get("first_seen_at") or now.isoformat()
            state["last_seen_at"] = now.isoformat()
            if self._is_cooldown_window_enabled() and state.get("blocked_until"):
                state["blocked_until"] = max(state.get("blocked_until"), now + timedelta(seconds=self._get_cooldown_seconds()))
            await self._save_state(key, state, ttl_seconds=window_seconds + self._get_cooldown_seconds())
            key_states.append(state)

        current_count = max(int(state.get("count", 0)) for state in key_states) if key_states else 0
        should_block = await self.should_block_request(
            failed_attempt_count=current_count,
            identifiers=identifiers,
            **payload,
        )
        backoff_delay = self.calculate_backoff_delay(failed_attempt_count=current_count)
        risk_score = await self.evaluate_brute_force_risk(
            failed_attempt_count=current_count,
            identifiers=identifiers,
            should_block=should_block,
            **payload,
        )

        if should_block and self.account_lockout_service is not None:
            try:
                await self.account_lockout_service.increment_failed_attempts(
                    user_id=user_id,
                    reason="brute_force_threshold_exceeded",
                    source="brute_force",
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "brute_force_account_lockout_notification_failed",
                    extra={"event_type": "security", "component": "BruteForceService", "error": str(exc)},
                )

        self._log_event(
            "brute_force_failed_attempt_recorded",
            user_id=str(user_id) if user_id is not None else None,
            identifiers=identifiers,
            attempt_count=current_count,
            should_block=should_block,
            risk_score=risk_score,
            backoff_delay=backoff_delay,
        )
        return {
            "success": True,
            "blocked": should_block,
            "should_block": should_block,
            "risk_score": risk_score,
            "backoff_delay": backoff_delay,
            "attempt_count": current_count,
            "window_seconds": window_seconds,
            "reason": "threshold_exceeded" if should_block else "under_threshold",
            "identifiers": identifiers,
        }

    async def record_successful_attempt(
        self,
        *,
        user_id: str | UUID | None = None,
        username: str | None = None,
        email: str | None = None,
        ip_address: str | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        session_id: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Reset brute-force counters after a successful authentication event."""
        identifiers = self._collect_identifiers(
            user_id=user_id,
            username=username,
            email=email,
            ip_address=ip_address,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            session_id=session_id,
        )
        if not identifiers:
            raise ValidationException("At least one identifier is required to clear brute-force counters.")
        await self.reset_failed_attempts(identifiers=identifiers, **payload)
        self._log_event(
            "brute_force_successful_attempt_recorded",
            user_id=str(user_id) if user_id is not None else None,
            identifiers=identifiers,
        )
        return {"success": True, "reset": True, "identifiers": identifiers}

    async def get_failed_attempt_count(
        self,
        *,
        user_id: str | UUID | None = None,
        username: str | None = None,
        email: str | None = None,
        ip_address: str | None = None,
        device_id: str | None = None,
        device_fingerprint: str | None = None,
        session_id: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Return the current failed-attempt count for the supplied identifier set."""
        identifiers = self._collect_identifiers(
            user_id=user_id,
            username=username,
            email=email,
            ip_address=ip_address,
            device_id=device_id,
            device_fingerprint=device_fingerprint,
            session_id=session_id,
        )
        counts: list[int] = []
        for identifier_type, identifier_value in identifiers.items():
            if not identifier_value:
                continue
            state = await self._load_state(self._counter_key(identifier_type, identifier_value))
            counts.append(int(state.get("count", 0)))
        current_count = max(counts) if counts else 0
        return {
            "success": True,
            "attempt_count": current_count,
            "identifiers": identifiers,
            "window_seconds": self._get_window_seconds(),
        }

    async def reset_failed_attempts(self, *, identifiers: dict[str, Any] | None = None, **payload: Any) -> dict[str, Any]:
        """Reset brute-force counters for the supplied identifier set."""
        effective_identifiers = identifiers or {}
        if not effective_identifiers:
            raise ValidationException("At least one identifier is required to reset brute-force counters.")
        for identifier_type, identifier_value in effective_identifiers.items():
            if not identifier_value:
                continue
            await self._delete_state(self._counter_key(identifier_type, identifier_value))
        self._log_event("brute_force_attempts_reset", identifiers=effective_identifiers)
        return {"success": True, "reset": True, "identifiers": effective_identifiers}

    async def should_block_request(
        self,
        *,
        failed_attempt_count: int | None = None,
        identifiers: dict[str, Any] | None = None,
        **payload: Any,
    ) -> bool:
        """Determine whether the request should be blocked based on the configured threshold."""
        threshold = self._get_threshold()
        if threshold is None:
            return False
        count = int(failed_attempt_count or 0)
        if count < threshold:
            return False
        if self._is_permanent_block_enabled() and count >= self._get_permanent_block_threshold():
            return True
        return True

    def calculate_backoff_delay(self, *, failed_attempt_count: int | None = None, **payload: Any) -> int:
        """Calculate the current backoff delay for repeated failed attempts."""
        attempts = max(int(failed_attempt_count or 0), 0)
        base_delay = self._get_setting("brute_force_backoff_base_seconds", self._get_setting("account_lock_duration_minutes", 15) * 60)
        multiplier = self._get_setting("brute_force_backoff_multiplier", 2)
        max_delay = self._get_setting("brute_force_backoff_max_seconds")
        if attempts <= 1:
            return int(base_delay)
        delay = int(base_delay * (multiplier ** max(0, attempts - 1)))
        if max_delay is not None:
            delay = min(delay, int(max_delay))
        return max(delay, 0)

    async def evaluate_brute_force_risk(
        self,
        *,
        failed_attempt_count: int | None = None,
        identifiers: dict[str, Any] | None = None,
        should_block: bool | None = None,
        **payload: Any,
    ) -> int:
        """Return a normalized risk score for the observed attack pattern."""
        attempts = max(int(failed_attempt_count or 0), 0)
        threshold = self._get_threshold() or 1
        score = min(100, int((attempts / max(threshold, 1)) * 100))
        if should_block:
            score = max(score, 80)
        if attempts >= max(3, threshold):
            score = max(score, 90)
        return score

    async def clear_expired_attempts(self, *, identifiers: dict[str, Any] | None = None, **payload: Any) -> dict[str, Any]:
        """Remove expired counters and stale attack state."""
        if identifiers:
            for identifier_type, identifier_value in identifiers.items():
                if not identifier_value:
                    continue
                await self._delete_state(self._counter_key(identifier_type, identifier_value))
        else:
            self._memory_store.clear()
        self._log_event("brute_force_expired_attempts_cleared", identifiers=identifiers or {})
        return {"success": True, "cleared": True, "identifiers": identifiers or {}}

    async def get_attack_statistics(self, *, identifiers: dict[str, Any] | None = None, **payload: Any) -> dict[str, Any]:
        """Return a structured snapshot of recent brute-force activity."""
        effective_identifiers = identifiers or {}
        stats: list[dict[str, Any]] = []
        for identifier_type, identifier_value in effective_identifiers.items():
            if not identifier_value:
                continue
            state = await self._load_state(self._counter_key(identifier_type, identifier_value))
            stats.append(
                {
                    "identifier_type": identifier_type,
                    "identifier_value": str(identifier_value),
                    "attempt_count": int(state.get("count", 0)),
                    "first_seen_at": state.get("first_seen_at"),
                    "last_seen_at": state.get("last_seen_at"),
                    "blocked": bool(state.get("blocked_until") and self._utc_now() < self._parse_timestamp(state.get("blocked_until"))),
                }
            )
        if not stats:
            stats = [{"identifier_type": "global", "identifier_value": "all", "attempt_count": 0, "first_seen_at": None, "last_seen_at": None, "blocked": False}]
        return {"success": True, "statistics": stats, "window_seconds": self._get_window_seconds()}

    async def _require_existing_user(self, user_id: str | UUID) -> User:
        if self.user_repository is None:
            return Any  # type: ignore[return-value]
        try:
            user_identifier = self._normalize_user_id(user_id)
        except ValidationException as exc:
            raise exc
        user = await self.user_repository.get_by_id(user_identifier)
        if user is None:
            raise ValidationException("User not found.")
        return user

    async def _load_state(self, key: str) -> dict[str, Any]:
        if self.redis_client is not None:
            try:
                payload = await self.redis_client.get(key)
                if payload:
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    state = json.loads(payload)
                    if isinstance(state, dict):
                        return state
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "brute_force_redis_load_failed",
                    extra={"event_type": "security", "component": "BruteForceService", "error": str(exc)},
                )
        return dict(self._memory_store.get(key, {}))

    async def _save_state(self, key: str, state: dict[str, Any], *, ttl_seconds: int) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.set(key, json.dumps(state), ex=max(60, ttl_seconds))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "brute_force_redis_save_failed",
                    extra={"event_type": "security", "component": "BruteForceService", "error": str(exc)},
                )
        self._memory_store[key] = dict(state)

    async def _delete_state(self, key: str) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.delete(key)
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "brute_force_redis_delete_failed",
                    extra={"event_type": "security", "component": "BruteForceService", "error": str(exc)},
                )
        self._memory_store.pop(key, None)

    def _collect_identifiers(
        self,
        *,
        user_id: str | UUID | None,
        username: str | None,
        email: str | None,
        ip_address: str | None,
        device_id: str | None,
        device_fingerprint: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        identifiers: dict[str, Any] = {}
        if user_id is not None:
            identifiers["user_id"] = str(user_id)
        if username:
            identifiers["username"] = username
        if email:
            identifiers["email"] = email
        if ip_address:
            identifiers["ip_address"] = ip_address
        if device_id:
            identifiers["device_id"] = device_id
        if device_fingerprint:
            identifiers["device_fingerprint"] = device_fingerprint
        if session_id:
            identifiers["session_id"] = session_id
        return identifiers

    def _counter_key(self, identifier_type: str, identifier_value: Any) -> str:
        normalized_value = str(identifier_value).strip()
        return f"{self._COUNTER_PREFIX}:{identifier_type}:{normalized_value}"

    def _get_threshold(self) -> int | None:
        threshold = self._get_setting("brute_force_max_attempts")
        if threshold is None:
            threshold = self._get_setting("max_login_attempts")
        if threshold is None:
            threshold = self._get_setting("max_failed_attempts")
        return int(threshold) if threshold is not None else None

    def _get_window_seconds(self) -> int:
        window_seconds = self._get_setting("brute_force_window_seconds")
        if window_seconds is None:
            window_seconds = self._get_setting("brute_force_time_window_seconds")
        if window_seconds is None:
            window_seconds = 900
        return int(window_seconds)

    def _get_cooldown_seconds(self) -> int:
        cooldown_seconds = self._get_setting("brute_force_cooldown_seconds")
        if cooldown_seconds is None:
            cooldown_seconds = self._get_setting("cooldown_seconds")
        if cooldown_seconds is None:
            cooldown_seconds = self._get_setting("account_lock_duration_minutes", 15) * 60
        return int(cooldown_seconds)

    def _is_cooldown_window_enabled(self) -> bool:
        return bool(self._get_setting("brute_force_cooldown_enabled", False))

    def _is_permanent_block_enabled(self) -> bool:
        return bool(self._get_setting("brute_force_permanent_block_enabled", False))

    def _get_permanent_block_threshold(self) -> int:
        threshold = self._get_setting("brute_force_permanent_block_threshold")
        if threshold is None:
            threshold = self._get_setting("permanent_lock_threshold")
        return int(threshold) if threshold is not None else 100

    def _get_setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _normalize_user_id(self, user_id: str | UUID) -> UUID:
        if isinstance(user_id, UUID):
            return user_id
        try:
            return UUID(str(user_id))
        except (TypeError, ValueError) as exc:
            raise ValidationException("User identifier is invalid.") from exc

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    def _log_event(self, event_name: str, **context: Any) -> None:
        self.logger.info(
            event_name,
            extra={"event_type": "security", "component": "BruteForceService", **context},
        )


__all__ = ["BruteForceService"]
