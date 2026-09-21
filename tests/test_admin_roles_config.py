import pytest

from app.main import create_app
from app.config import settings


def test_create_app_uses_conservative_admin_roles(monkeypatch):
    # Ensure no ADMIN_ALLOWED_ROLES configured
    monkeypatch.setattr(settings, "admin_allowed_roles", None)
    app = create_app()
    # Find AdminMiddleware in app.user_middleware
    admin_entry = None
    for m in app.user_middleware:
        if m.cls.__name__ == "AdminMiddleware":
            admin_entry = m
            break
    assert admin_entry is not None
    allowed = admin_entry.kwargs.get("allowed_roles")
    assert set(allowed) == {"super_admin", "admin"}
