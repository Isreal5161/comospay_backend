from __future__ import annotations

import ipaddress
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

for candidate_root in {
    Path(__file__).resolve().parents[3],
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend",
    Path(__file__).resolve().parents[3] / "CosmozPay-Backend" / "app",
}:
    if candidate_root.exists() and str(candidate_root) not in sys.path:
        sys.path.insert(0, str(candidate_root))

try:
    from app.config.settings import settings as default_settings
except Exception:  # pragma: no cover - compatibility fallback
    default_settings = None

try:
    from app.utils.exceptions import ValidationException
except Exception:  # pragma: no cover - compatibility fallback
    ValidationException = Exception  # type: ignore[misc]


@dataclass
class Location:
    ip_address: str
    country: Optional[str] = None
    region: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    timezone: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    isp: Optional[str] = None
    asn: Optional[str] = None
    continent: Optional[str] = None
    provider: Optional[str] = None
    resolved_at: Optional[str] = None


class GeoLocationService:
    """Enterprise GeoLocationService for location-based security analysis.

    Responsibilities:
    - Resolve an IP to geographic attributes
    - Provide helpers to read attributes (country, region, city, timezone)
    - Detect impossible travel between two locations
    - Score location risk using configurable weights and thresholds

    The implementation is provider-agnostic and supports an injected
    `geo_provider` with an async `lookup(ip)` method returning a mapping.
    Results are cached in Redis when available, with an in-memory fallback.
    """

    _CACHE_PREFIX = "geo_location"

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        settings_obj: Any | None = None,
        redis_client: Any | None = None,
        geo_provider: Any | None = None,
        risk_engine: Any | None = None,
        ip_reputation: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.settings = settings_obj or default_settings
        self.redis_client = redis_client
        self.geo_provider = geo_provider
        self.risk_engine = risk_engine
        self.ip_reputation = ip_reputation
        self._memory_store: Dict[str, Dict[str, Any]] = {}

    # -----------------
    # Public API
    # -----------------
    async def resolve_location(self, *, ip: str) -> Dict[str, Any]:
        """Resolve `ip` to a structured location.

        Returns a dict with country, region, state, city, timezone, latitude,
        longitude, isp, asn, continent and resolved_at (UTC isoformat).
        """
        normalized = self._normalize_ip(ip)
        cache_key = self._cache_key(normalized)
        cached = await self._load_cached(cache_key)
        if cached is not None:
            self._log_event("geo_cache_hit", ip=normalized)
            return dict(cached)

        self._log_event("geo_lookup", ip=normalized)
        # Provider lookup
        resolved: Dict[str, Any] = {
            "ip_address": normalized,
            "country": None,
            "region": None,
            "state": None,
            "city": None,
            "timezone": None,
            "latitude": None,
            "longitude": None,
            "isp": None,
            "asn": None,
            "continent": None,
            "provider": None,
            "resolved_at": self._utc_now().isoformat(),
        }

        if self.geo_provider is not None:
            try:
                provider_result = await self.geo_provider.lookup(normalized)
                if isinstance(provider_result, dict):
                    resolved.update({k: provider_result.get(k) for k in resolved.keys() if k in provider_result})
                    resolved["provider"] = getattr(self.geo_provider, "name", "external")
            except Exception as exc:  # pragma: no cover - provider errors
                self.logger.warning("geo_provider_lookup_failed", extra={"ip": normalized, "error": str(exc)})

        # Minimal best-effort fallback using nothing more than IP validation
        # (do not guess geographic fields).
        await self._save_cached(cache_key, resolved)
        return dict(resolved)

    async def get_country(self, *, ip: str) -> Optional[str]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("country")

    async def get_region(self, *, ip: str) -> Optional[str]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("region")

    async def get_state(self, *, ip: str) -> Optional[str]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("state")

    async def get_city(self, *, ip: str) -> Optional[str]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("city")

    async def get_timezone(self, *, ip: str) -> Optional[str]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("timezone")

    async def get_coordinates(self, *, ip: str) -> Tuple[Optional[float], Optional[float]]:
        loc = await self.resolve_location(ip=ip)
        return loc.get("latitude"), loc.get("longitude")

    def calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, float]:
        """Calculate great-circle distance between two coordinates.

        Returns kilometers and meters.
        """
        # Haversine formula
        R = 6371.0  # Earth radius in km
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        km = R * c
        return {"kilometers": km, "meters": km * 1000}

    def _coerce_coordinate(self, value: Any, *, field_name: str) -> float:
        """Coerce a coordinate value to a floating-point number."""
        if value is None:
            raise ValidationException(f"Location coordinates must include a numeric {field_name}.")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationException(f"Location coordinates must include a numeric {field_name}.") from exc

    async def detect_impossible_travel(self, *, previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        """Detect impossible travel between `previous` and `current` location records.

        Both records must include `latitude`, `longitude` and `resolved_at` (UTC isoformat).
        Returns a dict with distance_km, time_diff_seconds, speed_kmh and `impossible` boolean
        based on configured thresholds.
        """
        try:
            lat1 = self._coerce_coordinate(previous.get("latitude"), field_name="latitude")
            lon1 = self._coerce_coordinate(previous.get("longitude"), field_name="longitude")
            lat2 = self._coerce_coordinate(current.get("latitude"), field_name="latitude")
            lon2 = self._coerce_coordinate(current.get("longitude"), field_name="longitude")
        except ValidationException:
            raise ValidationException("Both previous and current locations must include numeric latitude and longitude.") from None

        dist = self.calculate_distance(lat1, lon1, lat2, lon2)["kilometers"]

        t1 = self._parse_time(previous.get("resolved_at"))
        t2 = self._parse_time(current.get("resolved_at"))
        if t1 is None or t2 is None:
            raise ValidationException("Both previous and current locations must include resolved_at timestamps in UTC isoformat.")

        time_diff = abs((t2 - t1).total_seconds())
        speed_kmh = self.calculate_travel_speed(distance_km=dist, time_seconds=time_diff)

        max_speed = float(self._get_setting("geo_impossible_travel_max_speed_kmh", 1000))
        min_time = int(self._get_setting("geo_impossible_travel_min_time_seconds", 60))

        impossible = False
        if time_diff >= min_time and speed_kmh > max_speed:
            impossible = True
            self._log_event("impossible_travel_detected", previous=previous.get("ip_address"), current=current.get("ip_address"), distance_km=dist, speed_kmh=speed_kmh)

        return {
            "distance_km": dist,
            "time_diff_seconds": time_diff,
            "speed_kmh": speed_kmh,
            "impossible": impossible,
        }

    def is_high_risk_country(self, *, country_code: Optional[str]) -> bool:
        if not country_code:
            return False
        high_risk = self._get_setting("geo_high_risk_countries", []) or []
        return str(country_code).upper() in {c.upper() for c in high_risk}

    def is_country_allowed(self, *, country_code: Optional[str]) -> bool:
        allow = self._get_setting("geo_allow_countries", None)
        if allow is None:
            return True
        return str(country_code).upper() in {c.upper() for c in (allow or [])}

    def is_country_blocked(self, *, country_code: Optional[str]) -> bool:
        blocked = self._get_setting("geo_block_countries", []) or []
        return str(country_code).upper() in {c.upper() for c in blocked}

    async def compare_previous_locations(self, *, previous_locations: List[Dict[str, Any]], current: Dict[str, Any]) -> Dict[str, Any]:
        """Compare current location against recent previous_locations.

        Returns a summary including whether country/city changed and counts of distinct countries.
        """
        curr_country = (current.get("country") or "").upper()
        curr_city = (current.get("city") or "").upper()
        distinct_countries = { (p.get("country") or "").upper() for p in previous_locations }
        country_changed = curr_country not in distinct_countries
        city_changed = curr_city not in { (p.get("city") or "").upper() for p in previous_locations }
        return {
            "country_changed": country_changed,
            "city_changed": city_changed,
            "distinct_country_count": len([c for c in distinct_countries if c]),
            "recent_countries": list(distinct_countries),
        }

    def calculate_travel_speed(self, *, distance_km: float, time_seconds: float) -> float:
        if time_seconds <= 0:
            return float("inf") if distance_km > 0 else 0.0
        hours = time_seconds / 3600.0
        return distance_km / hours

    async def get_location_risk_score(self, *, current: Dict[str, Any], previous_locations: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
        """Calculate a risk score and risk level for the supplied `current` location.

        Factors are configurable via settings. Returns a structured assessment.
        """
        score = 0
        details: Dict[str, Any] = {}

        country = (current.get("country") or "").upper()
        # high risk country
        if self.is_high_risk_country(country_code=country):
            w = float(self._get_setting("geo_weight_high_risk_country", 40))
            score += w
            details["high_risk_country"] = True
            self._log_event("high_risk_country_detected", country=country, ip=current.get("ip_address"))

        # blocked country
        if self.is_country_blocked(country_code=country):
            w = float(self._get_setting("geo_weight_blocked_country", 100))
            score += w
            details["blocked_country"] = True

        # allow list reduces score
        if not self.is_country_allowed(country_code=country):
            w = float(self._get_setting("geo_weight_not_allowed_country", 30))
            score += w
            details["not_allowed_country"] = True

        # compare with previous
        prevs = previous_locations or []
        cmp = await self.compare_previous_locations(previous_locations=prevs, current=current)
        if cmp.get("country_changed"):
            w = float(self._get_setting("geo_weight_new_country", 20))
            score += w
            details["new_country"] = True
        if cmp.get("city_changed"):
            w = float(self._get_setting("geo_weight_new_city", 10))
            score += w
            details["new_city"] = True

        # impossible travel
        if prevs:
            try:
                impossible = await self.detect_impossible_travel(previous=prevs[-1], current=current)
                if impossible.get("impossible"):
                    w = float(self._get_setting("geo_weight_impossible_travel", 50))
                    score += w
                    details["impossible_travel"] = impossible
            except ValidationException:
                # If travel detection can't be performed, ignore it gracefully.
                details["impossible_travel"] = {"error": "detection_failed"}

        # multiple rapid changes
        if len({ (p.get("country") or "").upper() for p in prevs[-3:] }) > 1:
            w = float(self._get_setting("geo_weight_rapid_country_changes", 15))
            score += w
            details["rapid_changes"] = True

        # Normalize and clamp
        max_score = float(self._get_setting("geo_max_score", 100))
        score = max(0.0, min(max_score, score))

        low = float(self._get_setting("geo_low_risk_threshold", 25))
        medium = float(self._get_setting("geo_medium_risk_threshold", 50))
        high = float(self._get_setting("geo_high_risk_threshold", 75))

        if score < low:
            level = "low"
        elif score < medium:
            level = "medium"
        elif score < high:
            level = "high"
        else:
            level = "critical"

        assessment = {
            "success": True,
            "ip_address": current.get("ip_address"),
            "country": current.get("country"),
            "city": current.get("city"),
            "latitude": current.get("latitude"),
            "longitude": current.get("longitude"),
            "score": score,
            "level": level,
            "details": details,
            "resolved_at": current.get("resolved_at"),
        }

        self._log_event("geo_assessment_generated", ip=current.get("ip_address"), score=score, level=level)
        return assessment

    # -----------------
    # Internal helpers
    # -----------------
    def _cache_key(self, ip: str) -> str:
        return f"{self._CACHE_PREFIX}:{ip}"

    async def _load_cached(self, key: str) -> Optional[Dict[str, Any]]:
        if self.redis_client is not None:
            try:
                payload = await self.redis_client.get(key)
                if payload:
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    obj = json.loads(payload)
                    if isinstance(obj, dict):
                        return obj
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning("geo_redis_load_failed", extra={"error": str(exc)})
        return self._memory_store.get(key)

    async def _save_cached(self, key: str, value: Dict[str, Any]) -> None:
        ttl = int(self._get_setting("geo_cache_ttl_seconds", self._get_setting("redis_cache_ttl", 300)))
        if self.redis_client is not None:
            try:
                await self.redis_client.set(key, json.dumps(value), ex=max(60, ttl))
            except Exception as exc:  # pragma: no cover - defensive fallback
                self.logger.warning("geo_redis_save_failed", extra={"error": str(exc)})
        self._memory_store[key] = dict(value)

    def _normalize_ip(self, ip: Optional[str]) -> str:
        if not ip:
            raise ValidationException("IP address is required")
        try:
            return str(ipaddress.ip_address(str(ip)))
        except ValueError as exc:  # pragma: no cover - defensive fallback
            raise ValidationException("Invalid IP address") from exc

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _parse_time(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).astimezone(timezone.utc)
        except Exception:
            return None

    def _get_setting(self, name: str, default: Any = None) -> Any:
        if self.settings is None:
            return default
        return getattr(self.settings, name, default)

    def _log_event(self, event_name: str, **context: Any) -> None:
        self.logger.info(event_name, extra={"event_type": "security", "component": "GeoLocationService", **context})


__all__ = ["GeoLocationService", "Location"]
