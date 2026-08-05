import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_auth_route_uses_dependency_injection_graph():
    module = importlib.import_module("app.routes.auth_routes")
    assert hasattr(module, "get_auth_service")
    assert hasattr(module, "get_auth_controller")
    assert module.router is not None


def test_user_route_uses_dependency_injection_graph():
    module = importlib.import_module("app.routes.user_routes")
    assert hasattr(module, "get_user_service")
    assert hasattr(module, "get_user_controller")
    assert module.router is not None


def test_giftcard_route_uses_dependency_injection_graph():
    module = importlib.import_module("app.routes.giftcard_routes")
    assert hasattr(module, "router")
