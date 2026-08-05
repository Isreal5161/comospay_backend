from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration for the Cosmozpay FastAPI backend.

    Settings are loaded from the project root .env file and validated at startup.
    The configuration is intentionally organized into service-oriented sections
    to keep the backend secure, maintainable, and production-ready.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Application
    app_name: str = Field(default="Cosmozpay", alias="APP_NAME")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    app_env: Literal["development", "testing", "production"] = Field(
        default="development",
        alias="APP_ENV",
    )
    debug: bool = Field(default=False, alias="DEBUG")

    # Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/cosmozpay",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, alias="DB_POOL_TIMEOUT")
    db_pool_recycle: int = Field(default=1800, alias="DB_POOL_RECYCLE")

    # JWT
    jwt_secret_key: SecretStr | None = Field(default=None, alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=30, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    # Redis
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    redis_cache_ttl: int = Field(default=300, alias="REDIS_CACHE_TTL")

    # Mail
    mail_provider: str = Field(default="smtp", alias="MAIL_PROVIDER")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: SecretStr | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")

    # Cloudinary
    cloudinary_cloud_name: str | None = Field(default=None, alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str | None = Field(default=None, alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: SecretStr | None = Field(default=None, alias="CLOUDINARY_API_SECRET")

    # Payment Providers
    flutterwave_public_key: str | None = Field(default=None, alias="FLUTTERWAVE_PUBLIC_KEY")
    flutterwave_secret_key: SecretStr | None = Field(default=None, alias="FLUTTERWAVE_SECRET_KEY")
    flutterwave_base_url: str | None = Field(default=None, alias="FLUTTERWAVE_BASE_URL")
    flutterwave_api_url: str | None = Field(default=None, alias="FLUTTERWAVE_API_URL")
    flutterwave_webhook_secret: SecretStr | None = Field(default=None, alias="FLUTTERWAVE_WEBHOOK_SECRET")
    paystack_secret_key: SecretStr | None = Field(default=None, alias="PAYSTACK_SECRET_KEY")
    monnify_api_key: str | None = Field(default=None, alias="MONNIFY_API_KEY")
    monnify_secret_key: SecretStr | None = Field(default=None, alias="MONNIFY_SECRET_KEY")
    korapay_secret_key: SecretStr | None = Field(default=None, alias="KORAPAY_SECRET_KEY")

    # VTU Providers
    aidapay_api_key: SecretStr | None = Field(default=None, alias="AIDAPAY_API_KEY")
    aidapay_base_url: str | None = Field(default=None, alias="AIDAPAY_BASE_URL")
    aidapay_account_pin: SecretStr | None = Field(default=None, alias="AIDAPAY_ACCOUNT_PIN")
    vtung_api_key: SecretStr | None = Field(default=None, alias="VTUNG_API_KEY")
    vtung_base_url: str | None = Field(default=None, alias="VTUNG_BASE_URL")
    clubkonnect_api_key: SecretStr | None = Field(default=None, alias="CLUBKONNECT_API_KEY")
    clubconnect_api_key: SecretStr | None = Field(default=None, alias="CLUBCONNECT_API_KEY")
    clubconnect_base_url: str | None = Field(default=None, alias="CLUBCONNECT_BASE_URL")
    vtugate_api_key: SecretStr | None = Field(default=None, alias="VTUGATE_API_KEY")
    vtugate_base_url: str | None = Field(default=None, alias="VTUGATE_BASE_URL")

    # SMS Providers
    termii_api_key: SecretStr | None = Field(default=None, alias="TERMII_API_KEY")
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: SecretStr | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")

    # Security
    password_min_length: int = Field(default=8, alias="PASSWORD_MIN_LENGTH")
    otp_length: int = Field(default=6, alias="OTP_LENGTH")
    otp_expiry_minutes: int = Field(default=10, alias="OTP_EXPIRY_MINUTES")
    max_login_attempts: int = Field(default=5, alias="MAX_LOGIN_ATTEMPTS")
    account_lock_duration_minutes: int = Field(default=15, alias="ACCOUNT_LOCK_DURATION_MINUTES")
    pin_length: int = Field(default=4, alias="PIN_LENGTH")

    # Provider Timeouts
    connection_timeout: int = Field(default=10, alias="CONNECTION_TIMEOUT")
    read_timeout: int = Field(default=10, alias="READ_TIMEOUT")
    write_timeout: int = Field(default=10, alias="WRITE_TIMEOUT")

    # Retry Configuration
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    retry_backoff_factor: float = Field(default=1.5, alias="RETRY_BACKOFF_FACTOR")
    max_retry_delay: int = Field(default=30, alias="MAX_RETRY_DELAY")

    # Virtual Account provisioning retry configuration
    # These control background retry behavior for provisioning dedicated virtual accounts.
    virtual_account_max_retries: int = Field(default=6, alias="VIRTUAL_ACCOUNT_MAX_RETRIES")
    virtual_account_initial_retry_delay_seconds: int = Field(
        default=60, alias="VIRTUAL_ACCOUNT_INITIAL_RETRY_DELAY_SECONDS"
    )
    virtual_account_backoff_multiplier: int = Field(default=2, alias="VIRTUAL_ACCOUNT_BACKOFF_MULTIPLIER")
    virtual_account_max_retry_delay_seconds: int = Field(default=86400, alias="VIRTUAL_ACCOUNT_MAX_RETRY_DELAY_SECONDS")
    virtual_account_retry_job_interval_seconds: int = Field(default=60, alias="VIRTUAL_ACCOUNT_RETRY_JOB_INTERVAL_SECONDS")
    virtual_account_retry_job_batch_size: int = Field(default=100, alias="VIRTUAL_ACCOUNT_RETRY_JOB_BATCH_SIZE")
    virtual_account_retry_job_lock_timeout_seconds: int = Field(
        default=60, alias="VIRTUAL_ACCOUNT_RETRY_JOB_LOCK_TIMEOUT_SECONDS"
    )
    virtual_account_retry_job_retry_interval_seconds: int = Field(
        default=60, alias="VIRTUAL_ACCOUNT_RETRY_JOB_RETRY_INTERVAL_SECONDS"
    )

    # Circuit Breaker
    failure_threshold: int = Field(default=5, alias="FAILURE_THRESHOLD")
    recovery_timeout: int = Field(default=60, alias="RECOVERY_TIMEOUT")
    half_open_max_calls: int = Field(default=3, alias="HALF_OPEN_MAX_CALLS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_directory: str = Field(default="logs", alias="LOG_DIRECTORY")

    # Security Headers
    security_headers_enabled: bool = Field(default=True, alias="SECURITY_HEADERS_ENABLED")

    # HSTS
    hsts_enabled: bool = Field(default=True, alias="HSTS_ENABLED")
    hsts_max_age: int = Field(default=31536000, alias="HSTS_MAX_AGE")
    hsts_include_subdomains: bool = Field(default=True, alias="HSTS_INCLUDE_SUBDOMAINS")
    hsts_preload: bool = Field(default=False, alias="HSTS_PRELOAD")

    # Content Security Policy
    content_security_policy: str = Field(
        default=(
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "img-src 'self' data:; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline';"
        ),
        alias="CONTENT_SECURITY_POLICY",
    )

    # Security Header Values
    x_frame_options: str = Field(default="DENY", alias="X_FRAME_OPTIONS")
    x_content_type_options: str = Field(default="nosniff", alias="X_CONTENT_TYPE_OPTIONS")
    referrer_policy: str = Field(default="no-referrer", alias="REFERRER_POLICY")
    permissions_policy: str = Field(default="geolocation=(), microphone=(), camera=()", alias="PERMISSIONS_POLICY")
    cross_origin_opener_policy: str = Field(default="same-origin", alias="CROSS_ORIGIN_OPENER_POLICY")
    cross_origin_resource_policy: str = Field(default="same-origin", alias="CROSS_ORIGIN_RESOURCE_POLICY")
    cross_origin_embedder_policy: str = Field(default="require-corp", alias="CROSS_ORIGIN_EMBEDDER_POLICY")

    @field_validator("app_env", mode="before")
    @classmethod
    def validate_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"development", "testing", "production"}:
                return normalized
        return value

    @model_validator(mode="after")
    def validate_required_settings(self) -> "Settings":
        def has_value(value: object) -> bool:
            if value is None:
                return False
            if isinstance(value, SecretStr):
                return bool(value.get_secret_value().strip())
            if isinstance(value, str):
                return bool(value.strip())
            return True

        if self.app_env == "production":
            required_fields = [
                ("database_url", "DATABASE_URL"),
                ("jwt_secret_key", "JWT_SECRET_KEY"),
                ("redis_url", "REDIS_URL"),
            ]
            missing = [name for field_name, name in required_fields if not has_value(getattr(self, field_name))]
            if missing:
                raise ValueError(
                    "Production configuration is incomplete. Missing required environment variables: "
                    + ", ".join(missing)
                )

        return self


settings = Settings()
