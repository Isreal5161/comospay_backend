from __future__ import annotations

import logging
from importlib import import_module
from typing import Any

from app.utils.exceptions import ValidationException


class SecurityService:
    """Thin orchestration facade for security capabilities.

    This service coordinates the specialized security sub-services and keeps
    business logic inside the dedicated modules under app/services/security/.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        account_lockout_service: Any | None = None,
        brute_force_service: Any | None = None,
        rate_limit_service: Any | None = None,
        ip_reputation_service: Any | None = None,
        geo_location_service: Any | None = None,
        trusted_device_service: Any | None = None,
        suspicious_login_service: Any | None = None,
        risk_engine_service: Any | None = None,
        security_event_service: Any | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.account_lockout_service = account_lockout_service or self._load_service(
            module_name="account_lockout",
            class_name="AccountLockoutService",
            fallback_name="AccountLockoutService",
        )
        self.brute_force_service = brute_force_service or self._load_service(
            module_name="brute_force",
            class_name="BruteForceService",
            fallback_name="BruteForceService",
        )
        self.rate_limit_service = rate_limit_service or self._load_service(
            module_name="rate_limit",
            class_name="RateLimitService",
            fallback_name="RateLimitService",
        )
        self.ip_reputation_service = ip_reputation_service or self._load_service(
            module_name="ip_reputation",
            class_name="IPReputationService",
            fallback_name="IPReputationService",
        )
        self.geo_location_service = geo_location_service or self._load_service(
            module_name="geo_location",
            class_name="GeoLocationService",
            fallback_name="GeoLocationService",
        )
        self.trusted_device_service = trusted_device_service or self._load_service(
            module_name="trusted_device",
            class_name="TrustedDeviceService",
            fallback_name="TrustedDeviceService",
        )
        self.suspicious_login_service = suspicious_login_service or self._load_service(
            module_name="suspicious_login",
            class_name="SuspiciousLoginService",
            fallback_name="SuspiciousLoginService",
        )
        self.risk_engine_service = risk_engine_service or self._load_service(
            module_name="risk_engine",
            class_name="RiskEngineService",
            fallback_name="RiskEngineService",
        )
        self.security_event_service = security_event_service or self._load_service(
            module_name="security_events",
            class_name="SecurityEventsService",
            fallback_name="SecurityEventsService",
        )

        self.logger.info(
            "security_service_initialized",
            extra={"event_type": "orchestration", "component": "SecurityService"},
        )

    def _load_service(self, *, module_name: str, class_name: str, fallback_name: str) -> Any:
        """Load and instantiate a concrete security service."""
        try:
            module = import_module(f"app.services.security.{module_name}")
        except ModuleNotFoundError as exc:
            raise RuntimeError(f"Security sub-service module '{module_name}' is unavailable.") from exc

        service_class = getattr(module, class_name, None)
        if service_class is None:
            raise RuntimeError(f"Security sub-service class '{class_name}' is unavailable.")

        try:
            return service_class(logger=self.logger)
        except TypeError:
            return service_class()

    async def _invoke_subservice(self, *, service: Any, method_names: tuple[str, ...], **payload: Any) -> Any:
        if service is None:
            raise ValidationException("Security sub-service is unavailable.")

        for method_name in method_names:
            method = getattr(service, method_name, None)
            if callable(method):
                return await method(**payload)
        raise ValidationException(f"No compatible method found for {method_names}.")

    async def lock_account(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate account locking to the account lockout service."""
        return await self.account_lockout_service.lock_account(user_id=user_id, **payload)

    async def unlock_account(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate account unlocking to the account lockout service."""
        return await self.account_lockout_service.unlock_account(user_id=user_id, **payload)

    async def is_account_locked(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate account state checks to the account lockout service."""
        return await self.account_lockout_service.is_account_locked(user_id=user_id, **payload)

    async def record_failed_login(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate failed-login tracking to the brute-force service."""
        return await self.brute_force_service.record_failed_login(user_id=user_id, **payload)

    async def reset_failed_attempts(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate failed-attempt reset to the brute-force service."""
        return await self.brute_force_service.reset_failed_attempts(user_id=user_id, **payload)

    async def check_rate_limit(self, *, key: str | None = None, **payload: Any) -> Any:
        """Delegate rate-limit evaluation to the rate-limit service."""
        return await self.rate_limit_service.check_rate_limit(key=key, **payload)

    async def validate_ip(self, *, ip_address: str | None = None, **payload: Any) -> Any:
        """Delegate IP reputation validation to the IP reputation service."""
        return await self._invoke_subservice(
            service=self.ip_reputation_service,
            method_names=("evaluate_ip", "get_ip_risk_score"),
            ip_address=ip_address,
            **payload,
        )

    async def get_ip_risk_score(self, *, ip_address: str | None = None, **payload: Any) -> Any:
        """Delegate IP risk scoring to the IP reputation service."""
        return await self._invoke_subservice(
            service=self.ip_reputation_service,
            method_names=("get_ip_risk_score", "evaluate_ip"),
            ip_address=ip_address,
            **payload,
        )

    async def validate_location(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate location validation to the geo-location service."""
        ip_address = payload.get("ip_address") or payload.get("ip")
        return await self._invoke_subservice(
            service=self.geo_location_service,
            method_names=("resolve_location",),
            ip=ip_address,
            **payload,
        )

    async def detect_impossible_travel(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate impossible-travel detection to the geo-location service."""
        return await self._invoke_subservice(
            service=self.geo_location_service,
            method_names=("detect_impossible_travel",),
            previous=payload.get("previous"),
            current=payload.get("current"),
            **payload,
        )

    async def verify_trusted_device(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate trusted-device verification to the trusted-device service."""
        return await self._invoke_subservice(
            service=self.trusted_device_service,
            method_names=("verify_device", "verify_trusted_device"),
            user_id=user_id,
            **payload,
        )

    async def trust_device(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate trusted-device registration to the trusted-device service."""
        return await self._invoke_subservice(
            service=self.trusted_device_service,
            method_names=("trust_device",),
            user_id=user_id,
            **payload,
        )

    async def remove_trusted_device(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate trusted-device removal to the trusted-device service."""
        return await self._invoke_subservice(
            service=self.trusted_device_service,
            method_names=("untrust_device", "remove_trusted_device"),
            user_id=user_id,
            **payload,
        )

    async def analyze_login_attempt(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate suspicious-login analysis to the suspicious-login service."""
        return await self._invoke_subservice(
            service=self.suspicious_login_service,
            method_names=("analyze_login_attempt",),
            user_id=user_id,
            **payload,
        )

    async def calculate_login_risk(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate login-risk calculation to the risk-engine service."""
        return await self._invoke_subservice(
            service=self.risk_engine_service,
            method_names=("calculate_risk_score", "calculate_login_risk"),
            user_id=user_id,
            **payload,
        )

    async def determine_security_action(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate security-action determination to the risk-engine service."""
        return await self._invoke_subservice(
            service=self.risk_engine_service,
            method_names=("determine_security_action",),
            user_id=user_id,
            **payload,
        )

    async def record_security_event(self, *, event_type: str | None = None, **payload: Any) -> Any:
        """Delegate security-event recording to the security-event service."""
        return await self._invoke_subservice(
            service=self.security_event_service,
            method_names=("create_security_event", "record_security_event"),
            event_type=event_type,
            **payload,
        )

    async def get_security_events(self, *, user_id: str | None = None, **payload: Any) -> Any:
        """Delegate security-event retrieval to the security-event service."""
        return await self._invoke_subservice(
            service=self.security_event_service,
            method_names=("get_security_events",),
            user_id=user_id,
            **payload,
        )


__all__ = ["SecurityService"]
