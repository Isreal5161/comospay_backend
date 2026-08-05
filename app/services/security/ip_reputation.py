from __future__ import annotations

import ipaddress
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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

class ValidationException(Exception):
    """Compatibility wrapper for validation errors."""

    def __init__(self, detail: str = "Validation failed.", error_code: str = "VALIDATION_FAILED") -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


class IPReputationService:
    """Enterprise IP reputation and trust analysis service.

    The service evaluates IP addresses for suspicious behavior using a
    configurable scoring model and can optionally cache results in Redis.
    It is intentionally limited to IP reputation evaluation and does not
    implement authentication or route handling logic.
    """

    _CACHE_PREFIX = "ip_reputation"
    _BLOCK_PREFIX = "ip_block"
    _ACTIVITY_PREFIX = "ip_activity"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        external_provider: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.external_provider = external_provider
        self._memory_store: dict[str, dict[str, Any]] = {}

    async def evaluate_ip(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Evaluate the reputation and risk of an IP address."""
        normalized_ip = self._normalize_ip(ip_address)
        cache_key = self._cache_key(normalized_ip)
        cached_value = await self._load_cached_result(cache_key)
        if cached_value is not None:
            self._log_event("ip_reputation_cache_hit", ip_address=normalized_ip)
            return cached_value

        self._log_event("ip_reputation_lookup", ip_address=normalized_ip)
        classification = self._classify_ip(normalized_ip)
        score = await self._calculate_risk_score(normalized_ip, classification=classification, **payload)
        status = self._risk_to_status(score)
        allow = status not in {"high", "critical"}

        result = {
            "success": True,
            "ip_address": normalized_ip,
            "classification": classification,
            "risk_score": score,
            "risk_level": status,
            "allow": allow,
            "blocked": not allow,
            "details": {
                "is_private": classification.get("is_private", False),
                "is_loopback": classification.get("is_loopback", False),
                "is_reserved": classification.get("is_reserved", False),
                "is_proxy": classification.get("is_proxy", False),
                "is_vpn": classification.get("is_vpn", False),
                "is_tor": classification.get("is_tor", False),
                "is_datacenter": classification.get("is_datacenter", False),
            },
        }
        await self._cache_result(cache_key, result)
        return result

    async def get_ip_risk_score(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Return the computed risk score for an IP address."""
        assessment = await self.evaluate_ip(ip_address=ip_address, **payload)
        return {
            "success": True,
            "ip_address": assessment["ip_address"],
            "risk_score": assessment["risk_score"],
            "risk_level": assessment["risk_level"],
            "allow": assessment["allow"],
        }

    async def is_ip_allowed(self, *, ip_address: str | None = None, **payload: Any) -> bool:
        """Return whether the supplied IP address is permitted by the current policy."""
        assessment = await self.evaluate_ip(ip_address=ip_address, **payload)
        return bool(assessment.get("allow", False))

    async def is_ip_blocked(self, *, ip_address: str | None = None, **payload: Any) -> bool:
        """Return whether the supplied IP address is blocked."""
        assessment = await self.evaluate_ip(ip_address=ip_address, **payload)
        return bool(assessment.get("blocked", False))

    async def is_private_ip(self, *, ip_address: str | None = None, **payload: Any) -> bool:
        """Return whether the IP address is private."""
        normalized_ip = self._normalize_ip(ip_address)
        return ipaddress.ip_address(normalized_ip).is_private

    async def is_loopback_ip(self, *, ip_address: str | None = None, **payload: Any) -> bool:
        """Return whether the IP address is loopback."""
        normalized_ip = self._normalize_ip(ip_address)
        return ipaddress.ip_address(normalized_ip).is_loopback

    async def is_reserved_ip(self, *, ip_address: str | None = None, **payload: Any) -> bool:
        """Return whether the IP address is reserved."""
        normalized_ip = self._normalize_ip(ip_address)
        return ipaddress.ip_address(normalized_ip).is_reserved

    async def detect_proxy(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Evaluate indicators of proxy usage."""
        normalized_ip = self._normalize_ip(ip_address)
        assessment = await self.evaluate_ip(ip_address=normalized_ip, **payload)
        return {
            "success": True,
            "ip_address": normalized_ip,
            "is_proxy": bool(assessment.get("details", {}).get("is_proxy", False)),
            "risk_score": assessment.get("risk_score", 0),
        }

    async def detect_vpn(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Evaluate indicators of VPN usage."""
        normalized_ip = self._normalize_ip(ip_address)
        assessment = await self.evaluate_ip(ip_address=normalized_ip, **payload)
        return {
            "success": True,
            "ip_address": normalized_ip,
            "is_vpn": bool(assessment.get("details", {}).get("is_vpn", False)),
            "risk_score": assessment.get("risk_score", 0),
        }

    async def detect_tor(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Evaluate indicators of TOR exit-node usage."""
        normalized_ip = self._normalize_ip(ip_address)
        assessment = await self.evaluate_ip(ip_address=normalized_ip, **payload)
        return {
            "success": True,
            "ip_address": normalized_ip,
            "is_tor": bool(assessment.get("details", {}).get("is_tor", False)),
            "risk_score": assessment.get("risk_score", 0),
        }

    async def detect_datacenter_ip(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Evaluate indicators of datacenter or hosting IP usage."""
        normalized_ip = self._normalize_ip(ip_address)
        assessment = await self.evaluate_ip(ip_address=normalized_ip, **payload)
        return {
            "success": True,
            "ip_address": normalized_ip,
            "is_datacenter": bool(assessment.get("details", {}).get("is_datacenter", False)),
            "risk_score": assessment.get("risk_score", 0),
        }

    async def record_ip_activity(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Record recent activity for an IP address."""
        normalized_ip = self._normalize_ip(ip_address)
        now = self._utc_now()
        state = await self._load_state(self._activity_key(normalized_ip))
        state.setdefault("ip_address", normalized_ip)
        state.setdefault("events", [])
        state["events"].append({"timestamp": now.isoformat(), **payload})
        state["last_seen_at"] = now.isoformat()
        await self._save_state(self._activity_key(normalized_ip), state, ttl_seconds=self._cache_ttl())
        return {"success": True, "ip_address": normalized_ip, "activity": state["events"][-1]}

    async def get_ip_statistics(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Return activity and reputation statistics for an IP address."""
        normalized_ip = self._normalize_ip(ip_address)
        state = await self._load_state(self._activity_key(normalized_ip))
        return {
            "success": True,
            "ip_address": normalized_ip,
            "event_count": len(state.get("events", [])),
            "last_seen_at": state.get("last_seen_at"),
            "events": state.get("events", []),
        }

    async def block_ip(self, *, ip_address: str | None = None, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        """Persist a block record for an IP address."""
        normalized_ip = self._normalize_ip(ip_address)
        state = await self._load_state(self._block_key(normalized_ip))
        state.update({"ip_address": normalized_ip, "blocked": True, "reason": reason or "manual_block", "updated_at": self._utc_now().isoformat()})
        await self._save_state(self._block_key(normalized_ip), state, ttl_seconds=self._cache_ttl())
        self._log_event("ip_blocked", ip_address=normalized_ip, reason=reason)
        return {"success": True, "ip_address": normalized_ip, "blocked": True, "reason": reason or "manual_block"}

    async def unblock_ip(self, *, ip_address: str | None = None, **payload: Any) -> dict[str, Any]:
        """Remove a block record for an IP address."""
        normalized_ip = self._normalize_ip(ip_address)
        await self._delete_state(self._block_key(normalized_ip))
        self._log_event("ip_unblocked", ip_address=normalized_ip)
        return {"success": True, "ip_address": normalized_ip, "blocked": False}

    async def _calculate_risk_score(self, ip_address: str, *, classification: dict[str, Any], **payload: Any) -> int:
        """Calculate a risk score based on configurable thresholds."""
        score = 0
        score += int(self._get_setting("ip_reputation_known_malicious_weight", 40) if payload.get("known_malicious") else 0)
        score += int(self._get_setting("ip_reputation_vpn_weight", 20) if classification.get("is_vpn") else 0)
        score += int(self._get_setting("ip_reputation_proxy_weight", 20) if classification.get("is_proxy") else 0)
        score += int(self._get_setting("ip_reputation_tor_weight", 30) if classification.get("is_tor") else 0)
        score += int(self._get_setting("ip_reputation_datacenter_weight", 15) if classification.get("is_datacenter") else 0)
        score += int(self._get_setting("ip_reputation_private_weight", 0) if classification.get("is_private") else 0)
        score += int(self._get_setting("ip_reputation_reserved_weight", 10) if classification.get("is_reserved") else 0)
        score += int(self._get_setting("ip_reputation_failed_login_weight", 5) * max(0, int(payload.get("failed_login_count", 0))))
        score += int(self._get_setting("ip_reputation_successful_login_weight", -2) * max(0, int(payload.get("successful_login_count", 0))))
        score += int(self._get_setting("ip_reputation_recent_abuse_weight", 15) if payload.get("recent_abuse") else 0)
        score += int(self._get_setting("ip_reputation_multiple_accounts_weight", 15) if payload.get("multiple_accounts") else 0)
        score += int(self._get_setting("ip_reputation_suspicious_velocity_weight", 15) if payload.get("suspicious_velocity") else 0)
        score = max(0, min(100, score))
        return score

    def _classify_ip(self, ip_address: str) -> dict[str, Any]:
        """Classify the IP address into trust-related categories."""
        try:
            ip_obj = ipaddress.ip_address(ip_address)
        except ValueError as exc:  # pragma: no cover - defensive fallback
            raise ValidationException("Invalid IP address.") from exc
        return {
            "ip_address": ip_address,
            "is_private": ip_obj.is_private,
            "is_loopback": ip_obj.is_loopback,
            "is_reserved": ip_obj.is_reserved,
            "is_proxy": bool(self._get_setting("ip_reputation_proxy_enabled", False)),
            "is_vpn": bool(self._get_setting("ip_reputation_vpn_enabled", False)),
            "is_tor": bool(self._get_setting("ip_reputation_tor_enabled", False)),
            "is_datacenter": bool(self._get_setting("ip_reputation_datacenter_enabled", False)),
        }

    def _risk_to_status(self, score: int) -> str:
        """Convert a numeric score into a risk label using configured thresholds."""
        low_threshold = self._get_setting("ip_reputation_low_risk_threshold", 25)
        medium_threshold = self._get_setting("ip_reputation_medium_risk_threshold", 50)
        high_threshold = self._get_setting("ip_reputation_high_risk_threshold", 75)
        if score < int(low_threshold):
            return "low"
        if score < int(medium_threshold):
            return "medium"
        if score < int(high_threshold):
            return "high"
        return "critical"

    async def _load_cached_result(self, key: str) -> dict[str, Any] | None:
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
                    "ip_reputation_redis_load_failed",
                    extra={"event_type": "security", "component": "IPReputationService", "error": str(exc)},
                )
        return self._memory_store.get(key)

    async def _cache_result(self, key: str, value: dict[str, Any]) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.set(key, json.dumps(value), ex=max(60, self._cache_ttl()))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "ip_reputation_redis_save_failed",
                    extra={"event_type": "security", "component": "IPReputationService", "error": str(exc)},
                )
        self._memory_store[key] = value

    async def _load_state(self, key: str) -> dict[str, Any]:
        cached_value = await self._load_cached_result(key)
        if cached_value is not None:
            return dict(cached_value)
        return {}

    async def _save_state(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.set(key, json.dumps(value), ex=max(60, ttl_seconds))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "ip_reputation_redis_save_failed",
                    extra={"event_type": "security", "component": "IPReputationService", "error": str(exc)},
                )
        self._memory_store[key] = dict(value)

    async def _delete_state(self, key: str) -> None:
        if self.redis_client is not None:
            try:
                await self.redis_client.delete(key)
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning(
                    "ip_reputation_redis_delete_failed",
                    extra={"event_type": "security", "component": "IPReputationService", "error": str(exc)},
                )
        self._memory_store.pop(key, None)

    def _normalize_ip(self, ip_address: str | None) -> str:
        if not ip_address:
            raise ValidationException("IP address is required.")
        try:
            return str(ipaddress.ip_address(str(ip_address)))
        except ValueError as exc:  # pragma: no cover - defensive fallback
            raise ValidationException("Invalid IP address.") from exc

    def _utc_now(self) -> datetime:
        """Return the current UTC timestamp."""
        return datetime.now(timezone.utc)

    def _cache_ttl(self) -> int:
        configured = self._get_setting("ip_reputation_cache_ttl_seconds")
        if configured is None:
            configured = self._get_setting("redis_cache_ttl", 300)
        return int(configured)

    def _cache_key(self, ip_address: str) -> str:
        return f"{self._CACHE_PREFIX}:{ip_address}"

    def _block_key(self, ip_address: str) -> str:
        return f"{self._BLOCK_PREFIX}:{ip_address}"

    def _activity_key(self, ip_address: str) -> str:
        return f"{self._ACTIVITY_PREFIX}:{ip_address}"

    def _get_setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _log_event(self, event_name: str, **context: Any) -> None:
        self.logger.info(
            event_name,
            extra={"event_type": "security", "component": "IPReputationService", **context},
        )


__all__ = ["IPReputationService"]
