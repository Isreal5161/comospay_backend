import pytest

from app.config import settings
from app.config.settings import Settings
from app.main import _get_cors_origins, _get_trusted_hosts


def test_production_wildcard_raises(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cors_allow_origins", ["*"])  # emulate setting
    with pytest.raises(RuntimeError):
        _get_cors_origins()


def test_production_missing_cors_origin_raises(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cors_allow_origins", None)
    with pytest.raises(RuntimeError):
        _get_cors_origins()


def test_production_missing_trusted_hosts_raises(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "trusted_hosts", None)
    with pytest.raises(RuntimeError):
        _get_trusted_hosts()


def test_production_wildcard_trusted_hosts_raise(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "trusted_hosts", ["*"])
    with pytest.raises(RuntimeError):
        _get_trusted_hosts()


def test_production_explicit_cors_and_trusted_hosts_are_allowed() -> None:
    settings_obj = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://prod_user:StrongProdPass!@prod-db.example.com:5432/cosmozpay",
        redis_url="redis://prod-redis.internal:6379/0",
        jwt_secret_key="a-very-secure-production-secret-32+chars",
        bank_account_encryption_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        cors_allow_origins=["https://admin.cosmozpay.com"],
        trusted_hosts=["admin.cosmozpay.com"],
    )

    assert settings_obj.cors_allow_origins == ["https://admin.cosmozpay.com"]
    assert settings_obj.trusted_hosts == ["admin.cosmozpay.com"]
