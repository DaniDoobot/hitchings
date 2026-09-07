"""Application settings management via pydantic-settings."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for HITCHINGS backend.
    
    Reads from environment variables and optionally from a .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General application
    PROJECT_NAME: str = "HITCHINGS"
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://hitchings:hitchings@db:5432/hitchings"

    # Bright Data (Reserved for future LinkedIn integration - Free-First policy)
    BRIGHTDATA_ENABLED: bool = False
    BRIGHTDATA_API_KEY: str = ""
    BRIGHTDATA_MONTHLY_LIMIT: int = 5000
    BRIGHTDATA_SOFT_LIMIT: int = 4500
    BRIGHTDATA_ALLOW_PAID_USAGE: bool = False

    # Apify (Reserved for future fallback - Free-First policy)
    APIFY_ENABLED: bool = False
    APIFY_API_KEY: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
