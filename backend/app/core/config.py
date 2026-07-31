import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_base_url: str = "http://localhost:8000"
    webhook_base_url: str = ""
    shop_dashboard_url: str = "http://localhost:3000"
    platform_dashboard_url: str = "http://localhost:3001"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    database_url: SecretStr = SecretStr(
        "postgresql://postgres:local-dev-password@localhost:5432/gents_saloon"
    )
    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_timeout_seconds: float = 5.0

    redis_url: SecretStr = SecretStr("redis://:local-dev-password@localhost:6379/0")
    celery_broker_url: SecretStr = SecretStr("redis://:local-dev-password@localhost:6379/0")
    celery_result_backend: SecretStr = SecretStr("redis://:local-dev-password@localhost:6379/0")

    moonshot_api_key: SecretStr = SecretStr("")
    moonshot_base_url: str = ""
    moonshot_model: str = ""
    token_encryption_key: SecretStr = SecretStr("")
    telegram_webhook_hmac_key: SecretStr = SecretStr("")
    telegram_webhook_max_body_bytes: int = Field(default=1_048_576, ge=1024, le=1_048_576)
    telegram_update_max_attempts: int = Field(default=5, ge=1, le=10)
    telegram_update_stale_seconds: int = Field(default=120, ge=30, le=900)
    telegram_update_retention_hours: int = Field(default=24, ge=24, le=72)
    telegram_flood_limit_per_minute: int = Field(default=20, ge=1, le=100)
    moonshot_timeout_seconds: float = Field(default=5.0, gt=0, le=5.0)
    ai_user_shop_hourly_budget: int = Field(default=20, ge=1, le=1000)
    ai_platform_daily_budget: int = Field(default=5000, ge=1, le=100000)
    export_storage_bucket: str = "tenant-exports"
    export_download_ttl_seconds: int = Field(default=900, ge=60, le=900)
    export_retention_hours: int = Field(default=72, ge=1, le=168)
    authenticated_rate_limit_per_minute: int = Field(default=120, ge=1, le=1000)
    platform_mutation_rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)
    public_rate_limit_per_minute: int = Field(default=300, ge=1, le=5000)

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()
        ]

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        for name, value, schemes in (
            ("DATABASE_URL", self.database_url.get_secret_value(), {"postgres", "postgresql"}),
            ("REDIS_URL", self.redis_url.get_secret_value(), {"redis", "rediss"}),
            ("CELERY_BROKER_URL", self.celery_broker_url.get_secret_value(), {"redis", "rediss"}),
            (
                "CELERY_RESULT_BACKEND",
                self.celery_result_backend.get_secret_value(),
                {"redis", "rediss"},
            ),
        ):
            if urlsplit(value).scheme not in schemes:
                raise ValueError(f"{name} has an unsupported URL scheme")

        if not self.allowed_origins:
            raise ValueError("CORS_ORIGINS must contain at least one exact origin")
        if self.supabase_jwks_timeout_seconds <= 0:
            raise ValueError("SUPABASE_JWKS_TIMEOUT_SECONDS must be greater than zero")

        for name, value in (
            ("TOKEN_ENCRYPTION_KEY", self.token_encryption_key.get_secret_value()),
            ("TELEGRAM_WEBHOOK_HMAC_KEY", self.telegram_webhook_hmac_key.get_secret_value()),
        ):
            if value:
                try:
                    decoded = base64.b64decode(value, altchars=b"-_", validate=True)
                except ValueError as exc:
                    raise ValueError(f"{name} must be valid base64") from exc
                if len(decoded) != 32:
                    raise ValueError(f"{name} must decode to exactly 32 bytes")

        if self.env == "production":
            if self.log_level == "DEBUG":
                raise ValueError("Production LOG_LEVEL cannot be DEBUG")

            required = {
                "SUPABASE_URL": self.supabase_url,
                "SUPABASE_ANON_KEY": self.supabase_anon_key.get_secret_value(),
                "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key.get_secret_value(),
                "WEBHOOK_BASE_URL": self.webhook_base_url,
                "TOKEN_ENCRYPTION_KEY": self.token_encryption_key.get_secret_value(),
                "TELEGRAM_WEBHOOK_HMAC_KEY": self.telegram_webhook_hmac_key.get_secret_value(),
                "EXPORT_STORAGE_BUCKET": self.export_storage_bucket,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(f"Missing production settings: {', '.join(missing)}")

            secure_urls = {
                "APP_BASE_URL": self.app_base_url,
                "WEBHOOK_BASE_URL": self.webhook_base_url,
                "SHOP_DASHBOARD_URL": self.shop_dashboard_url,
                "PLATFORM_DASHBOARD_URL": self.platform_dashboard_url,
                "SUPABASE_URL": self.supabase_url,
            }
            insecure = [
                name for name, value in secure_urls.items() if not value.startswith("https://")
            ]
            if insecure:
                raise ValueError(f"Production URLs must use HTTPS: {', '.join(insecure)}")
            if any(urlsplit(origin).scheme != "https" for origin in self.allowed_origins):
                raise ValueError("Production CORS origins must use HTTPS")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
