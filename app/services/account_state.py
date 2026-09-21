from __future__ import annotations

from typing import Any

from app.utils.exceptions import ValidationException


class UserAccountStateService:
    """Domain service for user account state transition rules."""

    _ALIASES = {
        "enable": "activate",
        "disable": "deactivate",
        "resume": "unsuspend",
        "unsuspend_user": "unsuspend",
        "email_verify": "verify_email",
        "phone_verify": "verify_phone",
    }

    _STATE_ACTIONS = {
        "activate": {"is_active": True, "is_suspended": False, "status": "active"},
        "deactivate": {"is_active": False, "status": "inactive"},
        "suspend": {"is_suspended": True, "status": "suspended"},
        "unsuspend": {"is_suspended": False, "status": "active"},
        "block": {"is_blocked": True, "status": "blocked"},
        "unblock": {"is_blocked": False, "status": "active"},
        "verify_email": {"email_verified": True},
        "unverify_email": {"email_verified": False},
        "verify_phone": {"phone_verified": True},
        "unverify_phone": {"phone_verified": False},
    }

    def normalize_transition_action(self, action: str) -> str:
        normalized = (action or "").strip().lower().replace("-", "_").replace(" ", "_")
        return self._ALIASES.get(normalized, normalized)

    def resolve_state_updates(self, *, action: str) -> dict[str, Any]:
        normalized = self.normalize_transition_action(action)
        updates = self._STATE_ACTIONS.get(normalized)
        if updates is None:
            raise ValidationException(detail="Unsupported management action.", error_code="USER_ACTION_INVALID")
        return dict(updates)
