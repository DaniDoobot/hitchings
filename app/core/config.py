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

    # AI Analysis (Bloque 7A - Architecture & Persistence Foundation)
    ANALYSIS_PROVIDER: str = "disabled"
    ANALYSIS_RELEVANT_MIN_SCORE: int = 70
    ANALYSIS_UNCERTAIN_MIN_SCORE: int = 40
    ANALYSIS_MONTHLY_ENTRY_LIMIT: int = 1000

    # Vertex AI (Bloque 7B - First Real AI Provider)
    # Authenticate locally with: gcloud auth application-default login
    # Do NOT store API keys or service account JSON in the repository.
    VERTEX_AI_PROJECT: str = ""
    VERTEX_AI_LOCATION: str = "global"
    VERTEX_AI_MODEL: str = "gemini-2.0-flash"

    # Thinking levels for each pipeline stage
    ANALYSIS_TRIAGE_THINKING_LEVEL: str = "low"
    ANALYSIS_DEEP_THINKING_LEVEL: str = "medium"

    # Maximum input characters per stage before raising AnalysisInputTooLarge
    ANALYSIS_TRIAGE_MAX_INPUT_CHARS: int = 250000
    ANALYSIS_DEEP_MAX_INPUT_CHARS: int = 250000

    # Benchmark safety budget (USD). Hard stop before starting each new Entry.
    ANALYSIS_BENCHMARK_MAX_USD: float = 1.00

    # Vertex AI pricing estimates for benchmark cost calculations.
    # These are configuration constants for gemini-2.0-flash as of 2026-09.
    # Review and update if Google changes pricing or if you switch models.
    # Unit: USD per 1,000,000 tokens.
    VERTEX_INPUT_USD_PER_MILLION_TOKENS: float = 0.075
    VERTEX_OUTPUT_USD_PER_MILLION_TOKENS: float = 0.30


@lru_cache
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
