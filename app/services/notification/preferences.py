from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.utils.exceptions import NotificationException, ValidationException
from app.utils.logger import log_audit_event


class NotificationPreferencesService:
    """Manage user notification preferences with repository persistence and Redis caching."""

    DEFAULT_FREQUENCY = "immediate"
    ALLOWED_CHANNELS = {"email", "sms", "push", "in_app"}
    ALLOWED_CATEGORIES = {"transactions", "promotions", "security", "system", "kyc", "marketing"}
    ALLOWED_FREQUENCIES = {"immediate", "daily", "weekly", "never"}
    QUIET_HOURS_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    def __init__(
        self,
        *,
        preferences_repository: Any,
        redis_client: Redis | None = None,
        logger: logging.Logger | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self.preferences_repository = preferences_repository
        self.redis_client = redis_client
        self.logger = logger or logging.getLogger(__name__)
        self.cache_ttl_seconds = cache_ttl_seconds

    async def get_preferences(self, *, user_id: UUID, force_refresh: bool = False) -> dict[str, Any]:
        """Retrieve cached preferences for a user, creating defaults when needed."""
        self._validate_user_id(user_id)
        cache_key = self._cache_key(user_id)

        if not force_refresh and self.redis_client is not None:
            cached = await self._get_cached_preferences(cache_key)
            if cached is not None:
                self.logger.info("notification_preferences_cache_hit", extra={"user_id": str(user_id)})
                return cached

        preferences = await self._fetch_preferences(user_id=user_id)
        if preferences is None:
            preferences = await self.create_default_preferences(user_id=user_id)

        await self._cache_preferences(cache_key, preferences)
        return preferences

    async def create_default_preferences(self, *, user_id: UUID) -> dict[str, Any]:
        """Create default notification preferences for a new user."""
        self._validate_user_id(user_id)
        preferences = self._default_preferences()

        try:
            saved = await self._save_preferences(user_id=user_id, preferences=preferences)
        except Exception as exc:
            self.logger.error(
                "notification_preferences_create_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise NotificationException(detail=str(exc)) from exc

        log_audit_event(
            self.logger,
            "notification_preferences_created",
            user_id=str(user_id),
            preferences=preferences,
        )
        return saved

    async def update_preferences(self, *, user_id: UUID, updates: dict[str, Any]) -> dict[str, Any]:
        """Update user notification preferences with validation and cache refresh."""
        self._validate_user_id(user_id)
        self._validate_update_payload(updates)

        current = await self.get_preferences(user_id=user_id)
        merged = self._merge_preferences(current, updates)
        self._validate_full_preferences(merged)

        try:
            saved = await self._save_preferences(user_id=user_id, preferences=merged)
        except Exception as exc:
            self.logger.error(
                "notification_preferences_update_failed",
                extra={"user_id": str(user_id), "error": str(exc), "updates": updates},
            )
            raise NotificationException(detail=str(exc)) from exc

        cache_key = self._cache_key(user_id)
        await self._cache_preferences(cache_key, saved)
        log_audit_event(
            self.logger,
            "notification_preferences_updated",
            user_id=str(user_id),
            updates=updates,
        )
        return saved

    async def enable_channel(self, *, user_id: UUID, channel: str, enabled: bool = True) -> dict[str, Any]:
        """Enable or disable a notification channel."""
        self._validate_channel(channel)
        return await self.update_preferences(user_id=user_id, updates={"channels": {channel: enabled}})

    async def set_global_notifications(self, *, user_id: UUID, enabled: bool) -> dict[str, Any]:
        """Toggle global notification enable/disable."""
        return await self.update_preferences(user_id=user_id, updates={"global_enabled": enabled})

    async def set_notification_frequency(self, *, user_id: UUID, frequency: str) -> dict[str, Any]:
        """Update notification frequency settings for a user."""
        self._validate_frequency(frequency)
        return await self.update_preferences(user_id=user_id, updates={"frequency": frequency})

    async def set_quiet_hours(
        self,
        *,
        user_id: UUID,
        start_time: str,
        end_time: str,
    ) -> dict[str, Any]:
        """Update quiet hours / do-not-disturb settings."""
        self._validate_quiet_hours(start_time=start_time, end_time=end_time)
        return await self.update_preferences(user_id=user_id, updates={"quiet_hours": {"start": start_time, "end": end_time}})

    async def set_category_preference(
        self,
        *,
        user_id: UUID,
        category: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Enable or disable a notification category."""
        self._validate_category(category)
        return await self.update_preferences(user_id=user_id, updates={"categories": {category: enabled}})

    async def enable_email(self, *, user_id: UUID, enabled: bool = True) -> dict[str, Any]:
        return await self.enable_channel(user_id=user_id, channel="email", enabled=enabled)

    async def enable_sms(self, *, user_id: UUID, enabled: bool = True) -> dict[str, Any]:
        return await self.enable_channel(user_id=user_id, channel="sms", enabled=enabled)

    async def enable_push(self, *, user_id: UUID, enabled: bool = True) -> dict[str, Any]:
        return await self.enable_channel(user_id=user_id, channel="push", enabled=enabled)

    async def enable_in_app(self, *, user_id: UUID, enabled: bool = True) -> dict[str, Any]:
        return await self.enable_channel(user_id=user_id, channel="in_app", enabled=enabled)

    def _default_preferences(self) -> dict[str, Any]:
        return {
            "user_id": None,
            "global_enabled": True,
            "channels": {
                "email": True,
                "sms": True,
                "push": True,
                "in_app": True,
            },
            "categories": {
                "transactions": True,
                "promotions": False,
                "security": True,
                "system": True,
                "kyc": True,
                "marketing": False,
            },
            "frequency": self.DEFAULT_FREQUENCY,
            "quiet_hours": None,
            "updated_at": self._now_iso(),
        }

    def _build_response(self, preferences: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": str(preferences.get("user_id")) if preferences.get("user_id") else None,
            "global_enabled": preferences.get("global_enabled", True),
            "channels": preferences.get("channels", {}),
            "categories": preferences.get("categories", {}),
            "frequency": preferences.get("frequency", self.DEFAULT_FREQUENCY),
            "quiet_hours": preferences.get("quiet_hours"),
            "updated_at": preferences.get("updated_at"),
        }

    def _validate_user_id(self, user_id: UUID) -> None:
        if not isinstance(user_id, UUID):
            raise ValidationException("Valid user identifier is required.")

    def _validate_update_payload(self, updates: dict[str, Any]) -> None:
        if not isinstance(updates, dict) or not updates:
            raise ValidationException("Preference updates must be a non-empty dictionary.")
        for key in updates:
            if key not in {"global_enabled", "channels", "categories", "frequency", "quiet_hours"}:
                raise ValidationException(f"Invalid preference field: {key}")
        if "channels" in updates:
            channels = updates["channels"]
            if not isinstance(channels, dict):
                raise ValidationException("Channels must be a mapping of channel names to enabled flags.")
            for channel_name, value in channels.items():
                self._validate_channel(channel_name)
                if not isinstance(value, bool):
                    raise ValidationException(f"Channel preference for {channel_name} must be true or false.")
        if "categories" in updates:
            categories = updates["categories"]
            if not isinstance(categories, dict):
                raise ValidationException("Categories must be a mapping of category names to enabled flags.")
            for category_name, value in categories.items():
                self._validate_category(category_name)
                if not isinstance(value, bool):
                    raise ValidationException(f"Category preference for {category_name} must be true or false.")
        if "frequency" in updates:
            self._validate_frequency(updates["frequency"])
        if "quiet_hours" in updates:
            quiet_hours = updates["quiet_hours"]
            if not isinstance(quiet_hours, dict):
                raise ValidationException("Quiet hours must be a mapping with start and end times.")
            self._validate_quiet_hours(
                start_time=quiet_hours.get("start", ""),
                end_time=quiet_hours.get("end", ""),
            )

    def _validate_full_preferences(self, preferences: dict[str, Any]) -> None:
        if not isinstance(preferences, dict):
            raise ValidationException("Preferences must be a mapping.")
        if "channels" not in preferences or not isinstance(preferences["channels"], dict):
            raise ValidationException("Preferences channels are required.")
        if "categories" not in preferences or not isinstance(preferences["categories"], dict):
            raise ValidationException("Preferences categories are required.")
        self._validate_frequency(preferences.get("frequency", self.DEFAULT_FREQUENCY))
        quiet_hours = preferences.get("quiet_hours")
        if quiet_hours is not None:
            self._validate_quiet_hours(
                start_time=quiet_hours.get("start", ""),
                end_time=quiet_hours.get("end", ""),
            )

    def _validate_channel(self, channel: str) -> None:
        if not isinstance(channel, str) or channel.strip().lower() not in self.ALLOWED_CHANNELS:
            raise ValidationException(f"Unsupported notification channel: {channel}")

    def _validate_category(self, category: str) -> None:
        if not isinstance(category, str) or category.strip().lower() not in self.ALLOWED_CATEGORIES:
            raise ValidationException(f"Unsupported notification category: {category}")

    def _validate_frequency(self, frequency: str) -> None:
        if not isinstance(frequency, str) or frequency.strip().lower() not in self.ALLOWED_FREQUENCIES:
            raise ValidationException(f"Unsupported notification frequency: {frequency}")

    def _validate_quiet_hours(self, *, start_time: str, end_time: str) -> None:
        if start_time is None or end_time is None:
            raise ValidationException("Quiet hours must include both start and end times.")
        if not isinstance(start_time, str) or not isinstance(end_time, str):
            raise ValidationException("Quiet hours start and end must be time strings.")
        if not self.QUIET_HOURS_PATTERN.match(start_time.strip()) or not self.QUIET_HOURS_PATTERN.match(end_time.strip()):
            raise ValidationException("Quiet hours must be formatted as HH:MM in 24-hour time.")

    async def _fetch_preferences(self, *, user_id: UUID) -> dict[str, Any] | None:
        if hasattr(self.preferences_repository, "get_preferences_by_user_id"):
            record = await self.preferences_repository.get_preferences_by_user_id(user_id)
        elif hasattr(self.preferences_repository, "get_by_user_id"):
            record = await self.preferences_repository.get_by_user_id(user_id)
        else:
            raise NotificationException("Preferences repository does not support retrieval by user id.")

        if record is None:
            return None
        return self._normalize_repository_result(record)

    async def _save_preferences(self, *, user_id: UUID, preferences: dict[str, Any]) -> dict[str, Any]:
        preferences_to_save = {**preferences, "user_id": str(user_id), "updated_at": self._now_iso()}

        if hasattr(self.preferences_repository, "update_preferences"):
            saved = await self.preferences_repository.update_preferences(user_id, preferences_to_save)
        elif hasattr(self.preferences_repository, "save_preferences"):
            saved = await self.preferences_repository.save_preferences(user_id, preferences_to_save)
        elif hasattr(self.preferences_repository, "create_preferences"):
            saved = await self.preferences_repository.create_preferences(preferences_to_save)
        else:
            raise NotificationException("Preferences repository does not support save operations.")

        return self._normalize_repository_result(saved) if saved is not None else preferences_to_save

    def _merge_preferences(self, current: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
        merged = {**current}
        for key, value in updates.items():
            if key in {"channels", "categories"} and isinstance(value, dict):
                merged[key] = {**merged.get(key, {}), **value}
            else:
                merged[key] = value
        merged["updated_at"] = self._now_iso()
        return merged

    async def _cache_preferences(self, cache_key: str, preferences: dict[str, Any]) -> None:
        if self.redis_client is None:
            return
        try:
            await self.redis_client.set(cache_key, json.dumps(preferences), ex=self.cache_ttl_seconds)
            self.logger.info("notification_preferences_cache_write", extra={"cache_key": cache_key})
        except Exception as exc:
            self.logger.warning("notification_preferences_cache_write_failed", extra={"cache_key": cache_key, "error": str(exc)})

    async def _get_cached_preferences(self, cache_key: str) -> dict[str, Any] | None:
        if self.redis_client is None:
            return None
        try:
            raw = await self.redis_client.get(cache_key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            self.logger.warning("notification_preferences_cache_read_failed", extra={"cache_key": cache_key, "error": str(exc)})
            return None

    def _normalize_repository_result(self, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return {**result, "updated_at": result.get("updated_at", self._now_iso())}

        if hasattr(result, "preferences"):
            preferences = result.preferences
        elif hasattr(result, "value"):
            preferences = result.value
        elif hasattr(result, "data"):
            preferences = result.data
        else:
            raise NotificationException("Unable to normalize preferences repository record.")

        if isinstance(preferences, str):
            try:
                preferences = json.loads(preferences)
            except json.JSONDecodeError as exc:
                raise NotificationException(detail=f"Stored preferences are malformed: {exc}") from exc

        if not isinstance(preferences, dict):
            raise NotificationException("Stored preferences are not a valid mapping.")

        preferences["updated_at"] = preferences.get("updated_at", self._now_iso())
        return preferences

    def _cache_key(self, user_id: UUID) -> str:
        return f"notification:preferences:{user_id}"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["NotificationPreferencesService"]
