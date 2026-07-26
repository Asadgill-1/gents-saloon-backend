import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_defaults_are_safe_and_parse_origins() -> None:
    settings = Settings(_env_file=None)

    assert settings.env == "development"
    assert settings.allowed_origins == ["http://localhost:3000", "http://localhost:3001"]
    assert "local-dev-password" not in repr(settings)


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="Missing production settings"):
        Settings(env="production", _env_file=None)


def test_production_rejects_insecure_origins() -> None:
    with pytest.raises(ValidationError, match="Production URLs must use HTTPS"):
        Settings(
            env="production",
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            webhook_base_url="https://api.example.com",
            master_bot_token="placeholder",
            token_encryption_key="placeholder",
            _env_file=None,
        )


def test_production_rejects_insecure_supabase_url() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        Settings(
            env="production",
            app_base_url="https://api.example.com",
            webhook_base_url="https://api.example.com/webhooks",
            shop_dashboard_url="https://shop.example.com",
            platform_dashboard_url="https://platform.example.com",
            cors_origins="https://shop.example.com,https://platform.example.com",
            supabase_url="http://example.supabase.co",
            supabase_anon_key="anon",
            supabase_service_role_key="service",
            master_bot_token="placeholder",
            token_encryption_key="placeholder",
            _env_file=None,
        )


def test_production_rejects_debug_logging() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(
            env="production",
            log_level="DEBUG",
            _env_file=None,
        )
