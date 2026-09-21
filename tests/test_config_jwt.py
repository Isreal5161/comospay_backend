import pytest
from datetime import timedelta

from app.config.jwt import decode_token
from app.services.auth.token_service import TokenService


def test_decode_token_delegates_and_parses():
    svc = TokenService()
    token = svc.create_access_token(subject="user-1")
    claims = decode_token(token)
    assert claims.get("sub") == "user-1"
    assert claims.get("type") == "access"


def test_decode_token_expired_raises_valueerror():
    svc = TokenService()
    token = svc.create_access_token(subject="user-1", ttl=timedelta(seconds=-10))
    with pytest.raises(ValueError):
        decode_token(token)
