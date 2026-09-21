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

    # CORS
    cors_allow_origins: list[str] | str | None = Field(default=["*"], alias="CORS_ALLOW_ORIGINS")
    cors_allow_methods: list[str] | str | None = Field(default=["*"], alias="CORS_ALLOW_METHODS")
    cors_allow_headers: list[str] | str | None = Field(default=["*"], alias="CORS_ALLOW_HEADERS")

    # Host and public path configuration
    trusted_hosts: list[str] | str | None = Field(default=["*"], alias="TRUSTED_HOSTS")
    public_routes: list[str] | str | None = Field(default=None, alias="PUBLIC_ROUTES")

    # Mail
    mail_provider: str = Field(default="smtp", alias="MAIL_PROVIDER")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: SecretStr | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")
    smtp_timeout_seconds: float = Field(default=10.0, alias="SMTP_TIMEOUT_SECONDS")
    frontend_url: str = Field(default="http://localhost:3000", alias="FRONTEND_URL")

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

    # Sogo Gift Card provider
    sogo_api_base_url: str | None = Field(default=None, alias="SOGO_API_BASE_URL")
    sogo_api_key: SecretStr | None = Field(default=None, alias="SOGO_API_KEY")
    sogo_webhook_secret: SecretStr | None = Field(default=None, alias="SOGO_WEBHOOK_SECRET")
    sogo_timeout_seconds: float = Field(default=10.0, alias="SOGO_TIMEOUT_SECONDS")

    # VTU Providers
    aidapay_api_key: SecretStr | None = Field(default=None, alias="AIDAPAY_API_KEY")
    aidapay_base_url: str | None = Field(default=None, alias="AIDAPAY_BASE_URL")
    aidapay_account_pin: SecretStr | None = Field(default=None, alias="AIDAPAY_ACCOUNT_PIN")
    vtung_api_key: SecretStr | None = Field(default=None, alias="VTUNG_API_KEY")
    vtung_base_url: str | None = Field(default=None, alias="VTUNG_BASE_URL")
    clubkonnect_api_key: SecretStr | None = Field(default=None, alias="CLUBKONNECT_API_KEY")
    clubconnect_api_key: SecretStr | None = Field(default=None, alias="CLUBCONNECT_API_KEY")
    clubconnect_base_url: str | None = Field(default=None, alias="CLUBCONNECT_BASE_URL")
    clubkonnect_user_id: str | None = Field(default=None, alias="CLUBKONNECT_USER_ID")
    clubconnect_user_id: str | None = Field(default=None, alias="CLUBCONNECT_USER_ID")
    clubkonnect_callback_url: str | None = Field(default=None, alias="CLUBKONNECT_CALLBACK_URL")
    clubconnect_callback_url: str | None = Field(default=None, alias="CLUBCONNECT_CALLBACK_URL")
    clubkonnect_phone_no: str | None = Field(default=None, alias="CLUBKONNECT_PHONE_NO")
    clubconnect_phone_no: str | None = Field(default=None, alias="CLUBCONNECT_PHONE_NO")
    clubkonnect_meter_type: str = Field(default="01", alias="CLUBKONNECT_METER_TYPE")
    clubconnect_meter_type: str = Field(default="01", alias="CLUBCONNECT_METER_TYPE")
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
    bank_account_encryption_key: SecretStr | None = Field(default=None, alias="BANK_ACCOUNT_ENCRYPTION_KEY")
    bank_account_encryption_key_version: int = Field(default=1, alias="BANK_ACCOUNT_ENCRYPTION_KEY_VERSION")

    # Admin configuration
    admin_allowed_roles: list[str] | None = Field(default=None, alias="ADMIN_ALLOWED_ROLES")

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

    @staticmethod
    def _normalize_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _secret_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, SecretStr):
            return value.get_secret_value().strip()
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _uses_default_local_database(value: str) -> bool:
        normalized = value.strip().lower()
        local_markers = (
            "postgresql://postgres:postgres@localhost",
            "postgresql+psycopg://postgres:postgres@localhost",
            "postgresql+asyncpg://postgres:postgres@localhost",
            "postgresql://postgres:postgres@127.0.0.1",
            "postgresql+psycopg://postgres:postgres@127.0.0.1",
            "postgresql+asyncpg://postgres:postgres@127.0.0.1",
        )
        return any(normalized.startswith(marker) for marker in local_markers)

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
                ("bank_account_encryption_key", "BANK_ACCOUNT_ENCRYPTION_KEY"),
            ]
            missing = [name for field_name, name in required_fields if not has_value(getattr(self, field_name))]
            if missing:
                raise ValueError(
                    "Production configuration is incomplete. Missing required environment variables: "
                    + ", ".join(missing)
                )

            if self._uses_default_local_database(self.database_url):
                raise ValueError(
                    "Production configuration is invalid: DATABASE_URL must not use the default local Postgres credentials."
                )

            algorithm = str(self.jwt_algorithm or "HS256").upper()
            secret_value = self._secret_value(self.jwt_secret_key)
            if algorithm == "HS256":
                if len(secret_value) < 32 or secret_value.lower() in {
                    "change-me-in-production",
                    "change-me",
                    "dev-secret",
                    "test-secret",
                    "secret",
                    "jwt-secret",
                    "default-secret",
                }:
                    raise ValueError(
                        "Production configuration is invalid: JWT_SECRET_KEY must be explicitly configured and at least 32 characters for HS256."
                    )
            elif algorithm == "RS256":
                pem_markers = (
                    "-----BEGIN PRIVATE KEY-----",
                    "-----BEGIN PUBLIC KEY-----",
                    "-----BEGIN RSA PRIVATE KEY-----",
                )
                if not any(marker in secret_value for marker in pem_markers):
                    raise ValueError(
                        "Production configuration is invalid: JWT_SECRET_KEY must be a PEM-formatted RSA key when JWT_ALGORITHM=RS256."
                    )
            else:
                raise ValueError(f"Production configuration is invalid: Unsupported JWT_ALGORITHM '{algorithm}'.")

            cors_origins = self._normalize_list(self.cors_allow_origins)
            if not cors_origins or any(origin == "*" for origin in cors_origins):
                raise ValueError(
                    "Production configuration is invalid: CORS allow_origins must be explicitly configured and must not contain wildcard '*'."
                )

            trusted_hosts = self._normalize_list(self.trusted_hosts)
            if not trusted_hosts or any(host == "*" for host in trusted_hosts):
                raise ValueError(
                    "Production configuration is invalid: trusted_hosts must be explicitly configured and must not contain wildcard '*'."
                )

        return self


settings = Settings()
