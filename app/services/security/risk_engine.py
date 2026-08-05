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


class RiskEngineService:
    """Enterprise scoring engine for security risk evaluation.

    The service aggregates multiple security signals and emits a normalized risk
    assessment that downstream components can consume without exposing internal
    scoring logic.
    """

    _CACHE_PREFIX = "risk_engine"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        trusted_device_service: Any | None = None,
        suspicious_login_service: Any | None = None,
        geo_location_service: Any | None = None,
        ip_reputation_service: Any | None = None,
        security_service: Any | None = None,
    ) -> None:
        """Initialize injectable collaborators used during scoring."""
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.trusted_device_service = trusted_device_service
        self.suspicious_login_service = suspicious_login_service
        self.geo_location_service = geo_location_service
        self.ip_reputation_service = ip_reputation_service
        self.security_service = security_service
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def calculate_risk_score(
        self,
        *,
        signals: Mapping[str, Any] | None = None,
        user_id: Any | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Calculate an enterprise risk assessment from security signals."""
        normalized_signals = await self.evaluate_security_signals(
            signals=signals,
            user_id=user_id,
            **payload,
        )
        risk_factors = await self.aggregate_risk_factors(normalized_signals)
        normalized_scores = self.normalize_scores(risk_factors)
        overall_score = int(sum(int(item.get("score", 0)) for item in normalized_scores))
        risk_level = self.classify_risk(overall_score)
        confidence = self._calculate_confidence(normalized_scores, overall_score)
        action = await self.determine_security_action(
            risk_level=risk_level,
            confidence=confidence,
            signals=normalized_signals,
            factors=normalized_scores,
        )
        summary = await self.generate_risk_summary(
            overall_score=overall_score,
            risk_level=risk_level,
            factors=normalized_scores,
            action=action,
            confidence=confidence,
            signals=normalized_signals,
        )

        await self._cache_result(self._cache_key(user_id), summary)
        return {
            "success": True,
            "risk_score": overall_score,
            "risk_level": risk_level,
            "confidence": confidence,
            "recommended_action": action.get("action"),
            "factors": normalized_scores,
            "summary": summary,
        }

    async def evaluate_security_signals(
        self,
        *,
        signals: Mapping[str, Any] | None = None,
        user_id: Any | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Validate and normalize incoming security signals for scoring."""
        if signals is None:
            signals = payload
        if not isinstance(signals, Mapping):
            raise ValidationException("Security signals must be a mapping.")

        normalized: dict[str, Any] = {
            "user_id": self._coerce_user_id(user_id or signals.get("user_id") or payload.get("user_id")),
            "device_trusted": bool(signals.get("device_trusted") or payload.get("device_trusted") or False),
            "device_fingerprint_mismatch": bool(signals.get("device_fingerprint_mismatch") or payload.get("device_fingerprint_mismatch") or False),
            "ip_risk_score": int(signals.get("ip_risk_score") or payload.get("ip_risk_score") or 0),
            "ip_risk_level": str(signals.get("ip_risk_level") or payload.get("ip_risk_level") or "low").lower(),
            "country_changed": bool(signals.get("country_changed") or payload.get("country_changed") or False),
            "city_changed": bool(signals.get("city_changed") or payload.get("city_changed") or False),
            "asn_changed": bool(signals.get("asn_changed") or payload.get("asn_changed") or False),
            "impossible_travel": bool(signals.get("impossible_travel") or payload.get("impossible_travel") or False),
            "login_velocity": int(signals.get("login_velocity") or payload.get("login_velocity") or 0),
            "failed_login_count": int(signals.get("failed_login_count") or payload.get("failed_login_count") or 0),
            "account_locked": bool(signals.get("account_locked") or payload.get("account_locked") or False),
            "unusual_time": bool(signals.get("unusual_time") or payload.get("unusual_time") or False),
            "unusual_behavior": bool(signals.get("unusual_behavior") or payload.get("unusual_behavior") or False),
            "device_switching_frequency": int(signals.get("device_switching_frequency") or payload.get("device_switching_frequency") or 0),
            "proxy_vpn_tor": bool(signals.get("proxy_vpn_tor") or payload.get("proxy_vpn_tor") or False),
            "jailbroken": bool(signals.get("jailbroken") or payload.get("jailbroken") or False),
            "existing_fraud_indicators": bool(signals.get("existing_fraud_indicators") or payload.get("existing_fraud_indicators") or False),
            "timestamp": self._normalize_timestamp(signals.get("timestamp") or payload.get("timestamp")),
            "metadata": dict(signals.get("metadata") or payload.get("metadata") or {}),
        }

        if normalized["user_id"] is None:
            raise ValidationException("User identifier is required.")

        return normalized

    def classify_risk(self, score: int | float) -> str:
        """Classify a numeric score into LOW, MEDIUM, HIGH, or CRITICAL."""
        normalized = int(score)
        critical_threshold = int(self._get_setting("security_login_critical_risk_threshold", 90))
        high_threshold = int(self._get_setting("security_login_high_risk_threshold", 70))
        medium_threshold = int(self._get_setting("security_login_medium_risk_threshold", 40))
        if normalized >= critical_threshold:
            return "CRITICAL"
        if normalized >= high_threshold:
            return "HIGH"
        if normalized >= medium_threshold:
            return "MEDIUM"
        return "LOW"

    async def determine_security_action(
        self,
        *,
        risk_level: str,
        confidence: float,
        signals: Mapping[str, Any],
        factors: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Recommend an appropriate security action from the assessed risk."""
        triggered = [factor for factor in factors if int(factor.get("score", 0)) > 0]
        if risk_level == "CRITICAL" or confidence <= 0.2:
            action = "temporarily_block_login"
            reasons = ["critical risk level", "low confidence"]
        elif risk_level == "HIGH" or len(triggered) >= 4:
            action = "require_mfa"
            reasons = ["high risk level", "multiple risk factors"]
        elif len(triggered) >= 2:
            action = "require_additional_verification"
            reasons = ["multiple risk factors"]
        elif signals.get("device_trusted") is False:
            action = "require_otp"
            reasons = ["untrusted device"]
        else:
            action = "allow"
            reasons = ["no strong risk signals"]

        if risk_level == "CRITICAL" and self._get_setting("security_login_escalate_to_security_service", False):
            action = "escalate_to_security_service"
            reasons.append("escalation required")
        elif risk_level == "HIGH" and self._get_setting("security_login_notify_user_on_high_risk", True):
            action = "notify_user"
            reasons.append("notify user")

        return {
            "action": action,
            "reasons": reasons,
            "risk_level": risk_level,
            "confidence": confidence,
            "triggered_factors": len(triggered),
        }

    async def score_device_risk(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score the device-related risk factors for a login attempt."""
        weight = int(self._get_setting("security_risk_weight_device", 15))
        score = 0
        reason = "Device appears trusted."
        if not bool(signals.get("device_trusted", False)):
            score += weight
            reason = "Device is not trusted."
        if bool(signals.get("device_fingerprint_mismatch", False)):
            score += int(self._get_setting("security_risk_weight_device_fingerprint", 20))
            reason = "Device fingerprint changed."
        if bool(signals.get("device_switching_frequency", 0)):
            score += int(self._get_setting("security_risk_weight_device_switching", 10))
            reason = "Device switching is frequent."
        return {"factor": "device", "score": score, "reason": reason}

    async def score_ip_risk(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score the IP-related risk factors for a login attempt."""
        score = 0
        reason = "IP reputation is acceptable."
        ip_risk_score = int(signals.get("ip_risk_score", 0))
        if ip_risk_score > 0:
            score += int(min(ip_risk_score, 40))
            reason = "IP reputation is elevated."
        if bool(signals.get("proxy_vpn_tor", False)):
            score += int(self._get_setting("security_risk_weight_proxy_vpn_tor", 20))
            reason = "IP shows proxy, VPN, or TOR indicators."
        return {"factor": "ip", "score": score, "reason": reason}

    async def score_location_risk(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score the location-related risk factors for a login attempt."""
        score = 0
        reason = "Location pattern is consistent."
        if bool(signals.get("country_changed", False)):
            score += int(self._get_setting("security_risk_weight_country_change", 15))
            reason = "Country changed from prior behavior."
        if bool(signals.get("city_changed", False)):
            score += int(self._get_setting("security_risk_weight_city_change", 10))
            reason = "City changed from prior behavior."
        if bool(signals.get("asn_changed", False)):
            score += int(self._get_setting("security_risk_weight_asn_change", 10))
            reason = "ASN or ISP changed."
        if bool(signals.get("impossible_travel", False)):
            score += int(self._get_setting("security_risk_weight_impossible_travel", 35))
            reason = "Impossible travel detected."
        return {"factor": "location", "score": score, "reason": reason}

    async def score_behavior_risk(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score the behavior-related risk factors for a login attempt."""
        score = 0
        reason = "Behavior is consistent."
        if bool(signals.get("unusual_time", False)):
            score += int(self._get_setting("security_risk_weight_unusual_time", 10))
            reason = "Login occurred at an unusual time."
        if bool(signals.get("unusual_behavior", False)):
            score += int(self._get_setting("security_risk_weight_unusual_behavior", 15))
            reason = "Behavioral anomaly detected."
        return {"factor": "behavior", "score": score, "reason": reason}

    async def score_login_velocity(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score suspicious login velocity."""
        velocity = int(signals.get("login_velocity", 0))
        threshold = int(self._get_setting("security_risk_weight_login_velocity_threshold", 3))
        weight = int(self._get_setting("security_risk_weight_login_velocity", 10))
        score = weight if velocity >= threshold else 0
        reason = "Login velocity is normal." if score == 0 else "Login velocity is unusually high."
        return {"factor": "login_velocity", "score": score, "reason": reason}

    async def score_failed_attempts(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score repeated failed login attempts."""
        count = int(signals.get("failed_login_count", 0))
        threshold = int(self._get_setting("security_risk_weight_failed_attempts_threshold", 3))
        weight = int(self._get_setting("security_risk_weight_failed_attempts", 15))
        score = weight if count >= threshold else 0
        reason = "Failed attempts are within normal bounds." if score == 0 else "Repeated failed login attempts were observed."
        return {"factor": "failed_attempts", "score": score, "reason": reason}

    async def score_account_status(self, *, signals: Mapping[str, Any]) -> dict[str, Any]:
        """Score account-state risk factors such as lockout state."""
        if bool(signals.get("account_locked", False)):
            return {"factor": "account_status", "score": int(self._get_setting("security_risk_weight_account_locked", 30)), "reason": "Account is locked or recently locked."}
        return {"factor": "account_status", "score": 0, "reason": "Account status is normal."}

    async def aggregate_risk_factors(self, signals: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Aggregate all individual factor scores into a unified list."""
        factors = [
            await self.score_device_risk(signals=signals),
            await self.score_ip_risk(signals=signals),
            await self.score_location_risk(signals=signals),
            await self.score_behavior_risk(signals=signals),
            await self.score_login_velocity(signals=signals),
            await self.score_failed_attempts(signals=signals),
            await self.score_account_status(signals=signals),
        ]
        return factors

    def normalize_scores(self, factors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Normalize factor scores to a comparable list of structured entries."""
        normalized = []
        for factor in factors:
            score = int(factor.get("score", 0))
            normalized.append({
                "factor": str(factor.get("factor") or "unknown"),
                "score": min(score, int(self._get_setting("security_risk_max_factor_score", 100))),
                "reason": str(factor.get("reason") or "No specific reason."),
            })
        return normalized

    async def generate_risk_summary(
        self,
        *,
        overall_score: int,
        risk_level: str,
        factors: Sequence[Mapping[str, Any]],
        action: Mapping[str, Any],
        confidence: float,
        signals: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create a structured summary of the final risk assessment."""
        return {
            "overall_score": overall_score,
            "risk_level": risk_level,
            "confidence": confidence,
            "recommended_action": action.get("action"),
            "triggered_factors": [factor.get("factor") for factor in factors if int(factor.get("score", 0)) > 0],
            "metadata": {
                "user_id": signals.get("user_id"),
                "timestamp": self._utc_now().isoformat(),
                "device_trusted": bool(signals.get("device_trusted", False)),
            },
        }

    async def get_risk_statistics(self, *, user_id: Any | None = None) -> dict[str, Any]:
        """Return risk statistics for a user from cache when available."""
        cache_key = self._cache_key(user_id)
        cached = await self._load_cached_result(cache_key)
        if cached is not None:
            return cached
        return {"success": True, "risk_score": 0, "risk_level": "LOW", "triggered_factors": []}

    def _coerce_user_id(self, value: Any) -> str | None:
        """Coerce a user identifier to a string value."""
        if value is None:
            return None
        return str(value)

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

    def _utc_now(self) -> datetime:
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc)

    def _calculate_confidence(self, factors: Sequence[Mapping[str, Any]], overall_score: int) -> float:
        """Calculate a normalized confidence score for the final assessment."""
        triggered_count = len([factor for factor in factors if int(factor.get("score", 0)) > 0])
        confidence = max(0.0, min(1.0, 0.6 + (triggered_count * 0.05)))
        if overall_score >= 80:
            confidence = max(confidence, 0.95)
        return round(confidence, 4)

    def _cache_key(self, user_id: Any | None) -> str:
        """Create a cache key for risk engine assessments."""
        return f"{self._CACHE_PREFIX}:{self._coerce_user_id(user_id) or 'unknown'}"

    async def _cache_result(self, cache_key: str, value: dict[str, Any]) -> None:
        """Cache a risk assessment in the configured store if available."""
        redis_client = self.redis_client
        if redis_client is None:
            self._memory_store[cache_key] = value
            return
        try:
            ttl_seconds = int(self._get_setting("redis_cache_ttl", 300))
            await redis_client.set(cache_key, json.dumps(value), ex=ttl_seconds)
        except Exception as exc:  # pragma: no cover - resilience
            self._memory_store[cache_key] = value
            self.logger.warning(
                "risk_engine_cache_failed",
                extra={"event_type": "security", "cache_key": cache_key, "error": str(exc)},
            )

    async def _load_cached_result(self, cache_key: str) -> dict[str, Any] | None:
        """Load a cached risk assessment if one exists."""
        redis_client = self.redis_client
        if redis_client is None:
            return self._memory_store.get(cache_key)
        try:
            payload = await redis_client.get(cache_key)
            if payload is None:
                return None
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            return data if isinstance(data, dict) else None
        except Exception:
            return self._memory_store.get(cache_key)

    def _get_setting(self, name: str, default: Any) -> Any:
        """Read a setting from the configured settings object with fallback defaults."""
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _log_event(self, event_name: str, **context: Any) -> None:
        """Emit a structured security log event."""
        self.logger.info(
            event_name,
            extra={"event_type": "security", "component": "RiskEngineService", **context},
        )


__all__ = ["RiskEngineService"]
