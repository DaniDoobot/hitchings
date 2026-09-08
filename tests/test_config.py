"""Tests for configuration and environment settings."""

from app.core.config import Settings


def test_default_settings() -> None:
    """Test default values of application settings."""
    settings = Settings()
    assert settings.PROJECT_NAME == "HITCHINGS"
    assert "postgresql+psycopg://" in settings.DATABASE_URL
    assert settings.BRIGHTDATA_ENABLED is False
    assert settings.BRIGHTDATA_MONTHLY_LIMIT == 5000
    assert settings.BRIGHTDATA_SOFT_LIMIT == 4500
    assert settings.BRIGHTDATA_ALLOW_PAID_USAGE is False
    assert settings.APIFY_ENABLED is False
    assert settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS == 0.75
    assert settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS == 3.75


def test_custom_settings_override() -> None:
    """Test that settings can be customized."""
    custom = Settings(
        PROJECT_NAME="HITCHINGS_TEST",
        BRIGHTDATA_MONTHLY_LIMIT=10000,
        BRIGHTDATA_ENABLED=True,
    )
    assert custom.PROJECT_NAME == "HITCHINGS_TEST"
    assert custom.BRIGHTDATA_MONTHLY_LIMIT == 10000
    assert custom.BRIGHTDATA_ENABLED is True
