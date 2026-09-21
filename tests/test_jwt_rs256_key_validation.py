import pytest

from types import SimpleNamespace

from app.services.auth.token_service import TokenService
from app.utils.exceptions import ValidationException


def test_rs256_requires_pem_key():
    fake_settings = SimpleNamespace()
    fake_settings.jwt_algorithm = "RS256"
    fake_settings.jwt_secret_key = "not-a-pem"
    fake_settings.access_token_expire_minutes = 60
    fake_settings.refresh_token_expire_days = 30

    svc = TokenService(settings_obj=fake_settings)
    with pytest.raises(ValidationException):
        svc._get_signing_key()
