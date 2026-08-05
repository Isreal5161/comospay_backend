from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

for candidate_root in {
    Path(__file__).resolve().parents[3],
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend",
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend" / "app",
}:
    if candidate_root.exists() and str(candidate_root) not in sys.path:
        sys.path.insert(0, str(candidate_root))

try:
    from app.config.redis import get_redis
except Exception:  # pragma: no cover - compatibility fallback
    get_redis = None

try:
    from app.config.settings import settings as default_settings
except Exception:  # pragma: no cover - compatibility fallback
    default_settings = None

try:
    from app.utils.exceptions import DatabaseException, ValidationException
except Exception:  # pragma: no cover - compatibility fallback
    class ValidationException(Exception):
        """Fallback validation exception for compatibility."""

    class DatabaseException(Exception):
        """Fallback database exception for compatibility."""

try:
    from app.utils.logger import log_security_event
except Exception:  # pragma: no cover - compatibility fallback
    def log_security_event(logger: logging.Logger, message: str, **context: Any) -> None:
        logger.warning(message, extra={"event_type": "security", **context})


class SuspiciousLoginService:
    """Enterprise business service for suspicious-login detection and assessment.

    This service is intentionally limited to security analysis. It does not
    authenticate users, issue tokens, or perform password verification.
    """

    _CACHE_PREFIX = "suspicious_login"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        trusted_device_service: Any | None = None,
        geo_location_service: Any | None = None,
        ip_reputation_service: Any | None = None,
        risk_engine_service: Any | None = None,
        user_repository: Any | None = None,
        security_service: Any | None = None,
    ) -> None:
        """Initialize injectable collaborators for suspicious-login analysis."""
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.trusted_device_service = trusted_device_service
        self.geo_location_service = geo_location_service
        self.ip_reputation_service = ip_reputation_service
        self.risk_engine_service = risk_engine_service
        self.user_repository = user_repository
        self.security_service = security_service
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def analyze_login_attempt(
        self,
        *,
        user_id: Any | None = None,
        login_context: Mapping[str, Any] | None = None,
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Analyze a login attempt and return a structured risk assessment."""
        normalized_context = await self.evaluate_login_context(
            user_id=user_id,
            login_context=login_context,
            previous_logins=previous_logins,
            **payload,
        )
        comparison = await self.compare_with_previous_logins(
            login_context=normalized_context,
            previous_logins=previous_logins,
        )
        detection = await self.detect_suspicious_login(
            login_context=normalized_context,
            previous_logins=previous_logins,
            comparison=comparison,
        )

        risk_score = int(detection.get("risk_score", 0))
        risk_level = detection.get("risk_level", self._risk_level_from_score(risk_score))
        confidence = await self.calculate_login_confidence(
            indicators=detection.get("indicators", []),
            risk_score=risk_score,
        )
        action = await self.determine_required_action(
            risk_level=risk_level,
            confidence=confidence,
            indicators=detection.get("indicators", []),
            login_context=normalized_context,
        )

        assessment = {
            "success": True,
            "user_id": normalized_context.get("user_id"),
            "risk_level": risk_level,
            "risk_score": risk_score,
            "confidence": confidence,
            "recommended_action": action.get("action"),
            "triggered_indicators": [indicator.get("indicator") for indicator in detection.get("indicators", []) if indicator.get("detected")],
            "indicators": detection.get("indicators", []),
            "suspicious": bool(detection.get("suspicious", False)),
            "comparison": comparison,
            "normalized_context": normalized_context,
        }

        alert: dict[str, Any] | None = None
        if assessment["suspicious"] or risk_level in {"HIGH", "CRITICAL"}:
            alert = await self.create_login_alert(
                login_context=normalized_context,
                assessment=assessment,
                action=action,
            )

        cache_key = self._cache_key(normalized_context.get("user_id"), normalized_context.get("ip_address"))
        await self._cache_result(cache_key, assessment)

        return {
            "success": True,
            "assessment": assessment,
            "alert": alert,
            "risk_summary": await self.get_login_risk_summary(login_context=normalized_context, assessment=assessment),
        }

    async def detect_suspicious_login(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Aggregate login-risk indicators into a single suspicious-login assessment."""
        comparison_data = dict(comparison or {})
        indicators: list[dict[str, Any]] = []

        travel_result = await self.detect_impossible_travel(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(travel_result)

        time_result = await self.detect_unusual_login_time(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(time_result)

        location_result = await self.detect_unusual_location(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(location_result)

        device_result = await self.detect_unusual_device(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(device_result)

        ip_result = await self.detect_unusual_ip(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(ip_result)

        behavior_result = await self.detect_behavior_anomalies(
            login_context=login_context,
            previous_logins=previous_logins,
            comparison=comparison_data,
        )
        indicators.append(behavior_result)

        triggered = [indicator for indicator in indicators if indicator.get("detected")]
        risk_score = int(sum(int(indicator.get("risk_points", 0)) for indicator in triggered))
        risk_level = self._risk_level_from_score(risk_score)
        suspicious = bool(triggered) and risk_level in {"HIGH", "CRITICAL"} or risk_score >= self._get_setting("security_login_suspicious_threshold", 40)

        return {
            "suspicious": suspicious,
            "indicators": indicators,
            "risk_score": risk_score,
            "risk_level": risk_level,
        }

    async def evaluate_login_context(
        self,
        *,
        user_id: Any | None = None,
        login_context: Mapping[str, Any] | None = None,
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Validate and normalize the supplied login context."""
        if login_context is None:
            raise ValidationException("Login context is required.")
        if not isinstance(login_context, Mapping):
            raise ValidationException("Login context must be a mapping.")

        resolved_user_id = self._coerce_user_id(user_id or login_context.get("user_id") or payload.get("user_id"))
        if not resolved_user_id:
            raise ValidationException("User identifier is required.")

        ip_address = self._coerce_value(login_context.get("ip_address") or login_context.get("ip") or payload.get("ip_address"), field_name="ip_address")
        if not ip_address:
            raise ValidationException("IP address is required.")

        device_data = login_context.get("device")
        if device_data is None:
            device_data = login_context.get("device_info") or {}
        if device_data is not None and not isinstance(device_data, Mapping):
            raise ValidationException("Invalid device information.")

        normalized_device = dict(device_data or {})
        device_id = normalized_device.get("device_id") or login_context.get("device_id") or payload.get("device_id")
        fingerprint = normalized_device.get("device_fingerprint") or login_context.get("device_fingerprint") or payload.get("device_fingerprint")
        browser = normalized_device.get("browser") or login_context.get("browser") or payload.get("browser")
        operating_system = normalized_device.get("operating_system") or login_context.get("operating_system") or payload.get("operating_system")

        trust_status = login_context.get("device_trust_status") or normalized_device.get("device_trust_status") or payload.get("device_trust_status")
        if trust_status is None and self.trusted_device_service is not None and device_id:
            try:
                result = await self.trusted_device_service.verify_device(
                    user_id=resolved_user_id,
                    device_id=device_id,
                    device_info=normalized_device,
                )
                trust_status = "trusted" if result.get("is_trusted") else "untrusted"
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "trusted_device_context_unavailable",
                    extra={"event_type": "security", "user_id": resolved_user_id, "error": str(exc)},
                )

        timestamp = self._normalize_timestamp(login_context.get("timestamp") or login_context.get("login_time") or payload.get("timestamp"))

        context = {
            "user_id": resolved_user_id,
            "ip_address": ip_address,
            "country": self._coerce_value(login_context.get("country") or payload.get("country"), field_name="country", nullable=True),
            "city": self._coerce_value(login_context.get("city") or payload.get("city"), field_name="city", nullable=True),
            "device_id": self._coerce_value(device_id, field_name="device_id", nullable=True),
            "device_fingerprint": self._coerce_value(fingerprint, field_name="device_fingerprint", nullable=True),
            "browser": self._coerce_value(browser, field_name="browser", nullable=True),
            "operating_system": self._coerce_value(operating_system, field_name="operating_system", nullable=True),
            "user_agent": self._coerce_value(login_context.get("user_agent") or payload.get("user_agent"), field_name="user_agent", nullable=True),
            "device_trust_status": trust_status,
            "timestamp": timestamp,
            "timestamp_hour": timestamp.hour,
            "failed_login_count": int(payload.get("failed_login_count") or login_context.get("failed_login_count") or 0),
            "login_frequency": int(payload.get("login_frequency") or login_context.get("login_frequency") or 0),
            "login_velocity": int(payload.get("login_velocity") or login_context.get("login_velocity") or 0),
            "metadata": dict(login_context.get("metadata") or payload.get("metadata") or {}),
            "previous_logins": list(previous_logins or []),
        }

        if context["device_id"] is None and context["device_fingerprint"] is None and context["user_agent"] is None:
            self.logger.warning(
                "login_context_missing_device_signal",
                extra={"event_type": "security", "user_id": resolved_user_id, "ip_address": ip_address},
            )

        return context

    async def compare_with_previous_logins(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compare the current login attempt against historical authentication behavior."""
        history = list(previous_logins or login_context.get("previous_logins") or [])
        current_ip = str(login_context.get("ip_address") or "")
        current_country = str(login_context.get("country") or "").upper()
        current_city = str(login_context.get("city") or "").upper()
        current_device = str(login_context.get("device_id") or login_context.get("device_fingerprint") or "")

        same_ip = any(str(item.get("ip_address") or "") == current_ip for item in history if isinstance(item, Mapping))
        same_country = any(str(item.get("country") or "").upper() == current_country for item in history if isinstance(item, Mapping) and current_country)
        same_city = any(str(item.get("city") or "").upper() == current_city for item in history if isinstance(item, Mapping) and current_city)
        same_device = any(str(item.get("device_id") or item.get("device_fingerprint") or "") == current_device for item in history if isinstance(item, Mapping) and current_device)

        timestamps = [self._normalize_timestamp(item.get("timestamp") or item.get("login_time")) for item in history if isinstance(item, Mapping)]
        timestamps = [value for value in timestamps if value is not None]
        timestamps.sort()
        recent_velocity = 0
        if len(timestamps) >= 2:
            recent_velocity = self._count_recent_events(timestamps)

        return {
            "previous_login_count": len(history),
            "same_ip": same_ip,
            "same_country": same_country,
            "same_city": same_city,
            "same_device": same_device,
            "country_changed": bool(current_country) and not same_country,
            "city_changed": bool(current_city) and not same_city,
            "device_changed": bool(current_device) and not same_device,
            "recent_velocity": recent_velocity,
            "recent_locations": [dict(item) for item in history if isinstance(item, Mapping)],
        }

    async def detect_impossible_travel(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Detect impossible-travel behavior using geographic context and prior locations."""
        if comparison is None:
            comparison = {}

        previous_locations = list(comparison.get("recent_locations") or [])
        if not previous_locations:
            return {"detected": False, "risk_points": 0, "indicator": "impossible_travel", "reason": "No historical location data."}

        previous_location = previous_locations[-1]
        current_location = {
            "latitude": login_context.get("latitude"),
            "longitude": login_context.get("longitude"),
            "resolved_at": login_context.get("timestamp"),
            "ip_address": login_context.get("ip_address"),
        }
        if self.geo_location_service is not None:
            try:
                result = await self.geo_location_service.detect_impossible_travel(previous=previous_location, current=current_location)
                if result.get("impossible"):
                    return {
                        "detected": True,
                        "risk_points": int(self._get_setting("security_login_impossible_travel_points", 35)),
                        "indicator": "impossible_travel",
                        "reason": "Impossible travel detected between locations.",
                    }
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "geo_location_detection_failed",
                    extra={"event_type": "security", "user_id": login_context.get("user_id"), "error": str(exc)},
                )

        return {"detected": False, "risk_points": 0, "indicator": "impossible_travel", "reason": "Travel pattern did not exceed thresholds."}

    async def detect_unusual_login_time(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Flag login attempts that occur during unusual hours."""
        hour = int(login_context.get("timestamp_hour") or 0)
        start_hour = int(self._get_setting("security_login_unusual_time_start_hour", 0))
        end_hour = int(self._get_setting("security_login_unusual_time_end_hour", 6))
        if start_hour <= end_hour:
            unusual = hour < start_hour or hour > end_hour
        else:
            unusual = hour < start_hour and hour > end_hour

        if unusual:
            return {
                "detected": True,
                "risk_points": int(self._get_setting("security_login_unusual_time_points", 15)),
                "indicator": "unusual_login_time",
                "reason": "Login occurred during unusual hours.",
            }
        return {"detected": False, "risk_points": 0, "indicator": "unusual_login_time", "reason": "Login time falls within expected hours."}

    async def detect_unusual_location(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Flag logins that change country or city from prior behavior."""
        comparison_data = dict(comparison or {})
        if comparison_data.get("country_changed") or comparison_data.get("city_changed"):
            return {
                "detected": True,
                "risk_points": int(self._get_setting("security_login_unusual_location_points", 20)),
                "indicator": "unusual_location",
                "reason": "Login location differs from prior history.",
            }
        return {"detected": False, "risk_points": 0, "indicator": "unusual_location", "reason": "Location matches prior pattern."}

    async def detect_unusual_device(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Flag new or untrusted devices using login context and historical behavior."""
        comparison_data = dict(comparison or {})
        trust_status = str(login_context.get("device_trust_status") or "unknown")
        if comparison_data.get("device_changed") or trust_status.lower() != "trusted":
            return {
                "detected": True,
                "risk_points": int(self._get_setting("security_login_unusual_device_points", 25)),
                "indicator": "unusual_device",
                "reason": "Login used a new or untrusted device.",
            }
        return {"detected": False, "risk_points": 0, "indicator": "unusual_device", "reason": "Device matches prior trust profile."}

    async def detect_unusual_ip(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Flag logins that use a new or risky IP address."""
        comparison_data = dict(comparison or {})
        ip_address = login_context.get("ip_address")
        risk_points = 0
        reason = "IP address matches historical pattern."

        if comparison_data.get("same_ip") is False:
            risk_points += int(self._get_setting("security_login_ip_change_points", 15))
            reason = "IP address changed from historical behavior."

        if self.ip_reputation_service is not None and ip_address:
            try:
                result = await self.ip_reputation_service.get_ip_risk_score(ip_address=ip_address)
                if result.get("risk_level") in {"high", "critical"}:
                    risk_points += int(self._get_setting("security_login_ip_reputation_points", 20))
                    reason = "IP reputation is high risk."
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "ip_reputation_lookup_failed",
                    extra={"event_type": "security", "user_id": login_context.get("user_id"), "error": str(exc)},
                )

        if risk_points > 0:
            return {"detected": True, "risk_points": risk_points, "indicator": "unusual_ip", "reason": reason}
        return {"detected": False, "risk_points": 0, "indicator": "unusual_ip", "reason": reason}

    async def detect_behavior_anomalies(
        self,
        *,
        login_context: Mapping[str, Any],
        previous_logins: Sequence[Mapping[str, Any]] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Flag abnormal frequency, velocity, or repeated failures in login behavior."""
        comparison_data = dict(comparison or {})
        risk_points = 0
        reason = "Behavior appears normal."

        failed_login_count = int(login_context.get("failed_login_count") or 0)
        login_frequency = int(login_context.get("login_frequency") or 0)
        login_velocity = int(login_context.get("login_velocity") or 0)
        previous_login_count = int(comparison_data.get("previous_login_count") or 0)
        recent_velocity = int(comparison_data.get("recent_velocity") or 0)

        if failed_login_count >= int(self._get_setting("security_login_failed_attempts_threshold", 3)):
            risk_points += int(self._get_setting("security_login_failed_attempts_points", 20))
            reason = "Repeated failed login attempts were observed."
        if login_frequency >= int(self._get_setting("security_login_frequency_threshold", 5)):
            risk_points += int(self._get_setting("security_login_frequency_points", 10))
            reason = "Login frequency is unusually high."
        if login_velocity >= int(self._get_setting("security_login_velocity_threshold", 3)) or recent_velocity >= int(self._get_setting("security_login_velocity_threshold", 3)):
            risk_points += int(self._get_setting("security_login_velocity_points", 10))
            reason = "Login velocity suggests automated activity."
        if previous_login_count >= int(self._get_setting("security_login_history_threshold", 5)):
            risk_points += int(self._get_setting("security_login_history_points", 5))
            reason = "Login history indicates repeated recent access."

        if risk_points > 0:
            return {"detected": True, "risk_points": risk_points, "indicator": "behavior_anomaly", "reason": reason}
        return {"detected": False, "risk_points": 0, "indicator": "behavior_anomaly", "reason": reason}

    async def calculate_login_confidence(
        self,
        *,
        indicators: Sequence[Mapping[str, Any]],
        risk_score: int | float,
    ) -> float:
        """Calculate a normalized confidence score for the assessed login attempt."""
        score = max(0.0, min(100.0, float(risk_score)))
        confidence = max(0.0, min(1.0, 1.0 - (score / 100.0)))
        if indicators:
            triggered = [indicator for indicator in indicators if indicator.get("detected")]
            if triggered:
                confidence = max(0.0, confidence - (0.03 * len(triggered)))
        return round(max(0.0, min(1.0, confidence)), 4)

    async def determine_required_action(
        self,
        *,
        risk_level: str,
        confidence: float,
        indicators: Sequence[Mapping[str, Any]],
        login_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Determine the recommended security action for the login attempt."""
        triggered_count = len([indicator for indicator in indicators if indicator.get("detected")])
        high_threshold = int(self._get_setting("security_login_high_risk_threshold", 70))
        critical_threshold = int(self._get_setting("security_login_critical_risk_threshold", 90))

        if risk_level == "CRITICAL" or confidence <= 0.2:
            action = "temporarily_block_login"
            reasons = ["critical risk level", "low confidence"]
        elif risk_level == "HIGH" or triggered_count >= 3:
            action = "require_additional_verification"
            reasons = ["high risk level", "multiple indicators"]
        elif triggered_count >= 1:
            action = "require_otp_verification"
            reasons = ["suspicious indicators detected"]
        else:
            action = "allow_login"
            reasons = ["no suspicious indicators"]

        if risk_level == "CRITICAL" and self._get_setting("security_login_escalate_to_security_service", False):
            action = "escalate_to_security_service"
            reasons.append("escalation required")
        elif risk_level == "HIGH" and triggered_count >= 2:
            action = "notify_user"
            reasons.append("user notification required")

        return {
            "action": action,
            "reasons": reasons,
            "risk_level": risk_level,
            "confidence": confidence,
            "triggered_indicators": triggered_count,
        }

    async def create_login_alert(
        self,
        *,
        login_context: Mapping[str, Any],
        assessment: Mapping[str, Any],
        action: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a structured login alert for a suspicious or high-risk login event."""
        user_id = login_context.get("user_id")
        alert = {
            "alert_id": self._new_alert_id(),
            "user_id": user_id,
            "risk_level": assessment.get("risk_level"),
            "risk_score": assessment.get("risk_score"),
            "confidence": assessment.get("confidence"),
            "recommended_action": action.get("action"),
            "triggered_indicators": assessment.get("triggered_indicators", []),
            "created_at": self._utc_now().isoformat(),
        }

        log_security_event(
            self.logger,
            "suspicious_login_alert_created",
            extra={
                "event_type": "security",
                "user_id": user_id,
                "risk_level": assessment.get("risk_level"),
                "action": action.get("action"),
                "indicator_count": len(assessment.get("triggered_indicators", [])),
            },
        )

        if self.security_service is not None:
            try:
                await self.security_service.record_security_event(
                    event_type="suspicious_login",
                    user_id=user_id,
                    alert=alert,
                )
            except Exception as exc:  # pragma: no cover - resilience
                self.logger.warning(
                    "security_service_notification_failed",
                    extra={"event_type": "security", "user_id": user_id, "error": str(exc)},
                )

        return alert

    async def get_login_risk_summary(
        self,
        *,
        login_context: Mapping[str, Any] | None = None,
        assessment: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a concise risk summary suitable for downstream consumers."""
        resolved_assessment = dict(assessment or {})
        return {
            "risk_level": resolved_assessment.get("risk_level", "LOW"),
            "risk_score": resolved_assessment.get("risk_score", 0),
            "confidence": resolved_assessment.get("confidence", 1.0),
            "recommended_action": resolved_assessment.get("recommended_action", "allow_login"),
            "suspicious": bool(resolved_assessment.get("suspicious", False)),
            "indicator_count": len(resolved_assessment.get("triggered_indicators", [])),
        }

    def _risk_level_from_score(self, risk_score: int | float) -> str:
        """Map a numeric risk score to the configured risk band."""
        score = int(risk_score)
        critical_threshold = int(self._get_setting("security_login_critical_risk_threshold", 90))
        high_threshold = int(self._get_setting("security_login_high_risk_threshold", 70))
        medium_threshold = int(self._get_setting("security_login_medium_risk_threshold", 40))
        if score >= critical_threshold:
            return "CRITICAL"
        if score >= high_threshold:
            return "HIGH"
        if score >= medium_threshold:
            return "MEDIUM"
        return "LOW"

    def _coerce_user_id(self, value: Any) -> str | None:
        """Coerce user identifiers to a string value."""
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        return str(value)

    def _coerce_value(self, value: Any, *, field_name: str, nullable: bool = False) -> Any:
        """Normalize a string-like context value while preserving nulls."""
        if value is None:
            return None if nullable else ""
        if isinstance(value, str):
            return value.strip() or (None if nullable else "")
        return value

    def _normalize_timestamp(self, value: Any) -> datetime:
        """Normalize a timestamp to a UTC-aware datetime instance."""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return self._utc_now()
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return self._utc_now()

    def _count_recent_events(self, timestamps: Sequence[datetime]) -> int:
        """Count how many recent events occurred within a short time window."""
        if len(timestamps) < 2:
            return 0
        recent_window_seconds = int(self._get_setting("security_login_recent_window_seconds", 300))
        latest = timestamps[-1]
        count = 0
        for timestamp in reversed(timestamps[:-1]):
            if (latest - timestamp).total_seconds() <= recent_window_seconds:
                count += 1
        return count

    def _utc_now(self) -> datetime:
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc)

    def _new_alert_id(self) -> str:
        """Create a stable alert identifier."""
        return f"login-alert-{self._utc_now().strftime('%Y%m%d%H%M%S')}-{abs(hash(self._utc_now()))}"

    def _get_setting(self, name: str, default: Any) -> Any:
        """Read a setting from the configured settings object, with a fallback default."""
        if self.settings is None:
            return default
        if hasattr(self.settings, name):
            return getattr(self.settings, name)
        if default_settings is not None and hasattr(default_settings, name):
            return getattr(default_settings, name)
        return default

    async def _cache_result(self, cache_key: str, value: dict[str, Any]) -> None:
        """Cache the assessment in Redis when available."""
        redis_client = await self._get_redis_client()
        if redis_client is None:
            self._memory_store[cache_key] = value
            return
        try:
            ttl_seconds = int(self._get_setting("redis_cache_ttl", 300))
            await redis_client.set(self._cache_key_name(cache_key), json.dumps(value), ex=ttl_seconds)
        except Exception as exc:  # pragma: no cover - resilience
            self._memory_store[cache_key] = value
            self.logger.warning(
                "login_assessment_cache_failed",
                extra={"event_type": "security", "cache_key": cache_key, "error": str(exc)},
            )

    async def _get_redis_client(self) -> Any | None:
        """Return a Redis client if one is configured or can be created."""
        if self.redis_client is not None:
            return self.redis_client
        if get_redis is None:
            return None
        try:
            return await get_redis()
        except TypeError:
            return get_redis()
        except Exception:
            return None

    def _cache_key(self, user_id: Any | None, ip_address: Any | None) -> str:
        """Build a cache key for a login assessment."""
        return f"{self._CACHE_PREFIX}:{self._coerce_user_id(user_id) or 'unknown'}:{ip_address or 'unknown'}"

    def _cache_key_name(self, cache_key: str) -> str:
        """Return a Redis-safe cache key."""
        return f"{self._CACHE_PREFIX}:{cache_key}"


__all__ = ["SuspiciousLoginService"]
