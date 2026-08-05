from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_production_requires_critical_security_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/cosmozpay",
            redis_url="redis://localhost:6379/0",
            jwt_secret_key=None,
        )


def test_development_supports_missing_optional_provider_credentials() -> None:
    settings = Settings(
        app_env="development",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/cosmozpay",
        jwt_secret_key="dev-secret",
    )

    assert settings.app_env == "development"
    assert settings.aidapay_api_key is None


def test_flutterwave_settings_are_loaded_from_environment_aliases() -> None:
    settings = Settings(
        app_env="development",
        flutterwave_public_key="flutterwave-public",
        flutterwave_secret_key="flutterwave-secret",
        flutterwave_base_url="https://api.flutterwave.com/v3",
        flutterwave_webhook_secret="webhook-secret",
    )

    assert settings.flutterwave_public_key == "flutterwave-public"
    assert settings.flutterwave_secret_key is not None
    assert settings.flutterwave_secret_key.get_secret_value() == "flutterwave-secret"
    assert settings.flutterwave_base_url == "https://api.flutterwave.com/v3"
    assert settings.flutterwave_webhook_secret is not None
    assert settings.flutterwave_webhook_secret.get_secret_value() == "webhook-secret"


def test_gitignore_blocks_env_file_from_source_control() -> None:
    gitignore_path = Path(__file__).resolve().parents[1] / ".gitignore"
    contents = gitignore_path.read_text(encoding="utf-8")

    assert ".env" in contents
    assert ".env.example" not in contents or "!.env.example" in contents
