"""Application settings management via pydantic-settings."""

from functools import lru_cache
import json
from typing import Any, Union, Optional
from pydantic import field_validator
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

    # Web & CORS (Bloque 8B.0 - Frontend Portal Preparation)
    CORS_ALLOWED_ORIGINS: Union[list[str], str] = []
    FRONTEND_URL: str = ""

    @field_validator("CORS_ALLOWED_ORIGINS", mode="after")
    @classmethod
    def assemble_cors_origins(cls, v: Any) -> list[str]:
        """Parse comma-separated strings or lists into a clean list of origins."""
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str:
                return []
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    parsed = json.loads(v_str)
                    if isinstance(parsed, list):
                        return [str(i).strip() for i in parsed if str(i).strip()]
                except Exception:
                    pass
            return [i.strip() for i in v_str.split(",") if i.strip()]
        elif isinstance(v, (list, tuple, set)):
            return [str(i).strip() for i in v if str(i).strip()]
        return []

    # Database
    DATABASE_URL: str = "postgresql+psycopg://hitchings:hitchings@db:5432/hitchings"

    # Bright Data (Social/anti-bot sources - Free-First policy)
    BRIGHTDATA_ENABLED: bool = False
    BRIGHTDATA_API_KEY: str = ""
    BRIGHTDATA_API_TOKEN: str = ""
    BRIGHTDATA_MONTHLY_LIMIT: int = 5000
    BRIGHTDATA_SOFT_LIMIT: int = 4500
    BRIGHTDATA_ALLOW_PAID_USAGE: bool = False
    BRIGHTDATA_LINKEDIN_ENDPOINT: str = "https://api.brightdata.com/datasets/v3/scrape"
    BRIGHTDATA_LINKEDIN_DATASET_ID: str = "gd_lyy3tktm25m4avu764"
    BRIGHTDATA_COST_PER_RECORD_USD: Optional[float] = None

    # Apify (Alternative/fallback social scraping - Free-First policy)
    APIFY_ENABLED: bool = False
    APIFY_API_KEY: str = ""
    APIFY_API_TOKEN: str = ""
    APIFY_LINKEDIN_ENDPOINT: str = "https://api.apify.com/v2/acts"
    APIFY_LINKEDIN_ACTOR_ID: str = "harvestapi/linkedin-post-search"
    APIFY_COST_PER_RECORD_USD: Optional[float] = None

    # AI Analysis (Bloque 7A - Architecture & Persistence Foundation)
    ANALYSIS_PROVIDER: str = "disabled"
    ANALYSIS_RELEVANT_MIN_SCORE: int = 70
    ANALYSIS_UNCERTAIN_MIN_SCORE: int = 40
    ANALYSIS_MONTHLY_ENTRY_LIMIT: int = 1000

    # Gemini Developer API (Bloque 7B - First Real AI Provider)
    # Authenticate with API key from Google AI Studio / Gemini Developer API.
    # In production (Dokploy/VPS), configure as an environment secret.
    # Never store API keys in the repository.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.8-flash"

    # Thinking levels for each pipeline stage (low/medium/high)
    ANALYSIS_TRIAGE_THINKING_LEVEL: str = "low"
    ANALYSIS_DEEP_THINKING_LEVEL: str = "medium"

    # Maximum input characters per stage before raising AnalysisInputTooLarge
    ANALYSIS_TRIAGE_MAX_INPUT_CHARS: int = 250000
    ANALYSIS_DEEP_MAX_INPUT_CHARS: int = 250000

    # Benchmark safety budget (USD). Hard stop before starting each new Entry.
    ANALYSIS_BENCHMARK_MAX_USD: float = 1.00

    # Gemini Developer API pricing estimates for benchmark cost calculations.
    # Official pricing for gemini-3.8-flash (September 2026):
    # $0.75 per 1M input tokens, $3.75 per 1M output tokens (includes reasoning).
    # Review and update if Google changes pricing or if you switch models.
    # Unit: USD per 1,000,000 tokens.
    GEMINI_INPUT_USD_PER_MILLION_TOKENS: float = 0.75
    GEMINI_OUTPUT_USD_PER_MILLION_TOKENS: float = 3.75

    # Authentication & Sessions (Bloque 8C.1 - Private Portal Authentication)
    AUTH_COOKIE_NAME: str = "hitchings_session"
    AUTH_SESSION_TTL_HOURS: int = 24
    AUTH_COOKIE_SECURE: bool = False
    AUTH_COOKIE_DOMAIN: Optional[str] = None

    # Google News Discovery (Bloque 9A - Discovery Source)
    GOOGLE_NEWS_ENABLED: bool = False
    GOOGLE_NEWS_LANGUAGES: Union[list[str], str] = ["es", "en"]
    GOOGLE_NEWS_REGION: str = "ES"
    GOOGLE_NEWS_MAX_QUERIES_PER_RUN: int = 20
    GOOGLE_NEWS_MAX_ITEMS_PER_QUERY: int = 10
    GOOGLE_NEWS_MAX_NEW_ENTRIES_PER_RUN: int = 50
    GOOGLE_NEWS_TIMEOUT_SECONDS: float = 15.0

    # Direct Web Sources (Bloque 9B - Reference Web & Blog Sources)
    DIRECT_WEB_INGESTION_ENABLED: bool = False
    DIRECT_WEB_MAX_ITEMS_PER_SOURCE: int = 20
    DIRECT_WEB_TIMEOUT_SECONDS: float = 15.0
    DIRECT_WEB_MAX_RESPONSE_BYTES: int = 5_000_000
    DIRECT_WEB_MAX_CONTENT_CHARS: int = 250_000

    # LinkedIn Discovery (Bloque 9C - Provider Discovery)
    LINKEDIN_DISCOVERY_ENABLED: bool = False
    LINKEDIN_PRIMARY_PROVIDER: str = "brightdata"
    LINKEDIN_FALLBACK_PROVIDER: str = "apify"
    LINKEDIN_MAX_ENTITIES_PER_RUN: int = 10
    LINKEDIN_MAX_POSTS_PER_ENTITY: int = 5
    LINKEDIN_MAX_NEW_ENTRIES_PER_RUN: int = 25
    LINKEDIN_TIMEOUT_SECONDS: float = 60.0
    LINKEDIN_MAX_POST_CHARS: int = 50_000

    @field_validator("GOOGLE_NEWS_LANGUAGES", mode="after")
    @classmethod
    def assemble_languages(cls, v: Any) -> list[str]:
        """Parse comma-separated strings or lists into clean lowercase language codes."""
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str:
                return ["es", "en"]
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    parsed = json.loads(v_str)
                    if isinstance(parsed, list):
                        return [str(i).strip().lower() for i in parsed if str(i).strip()]
                except Exception:
                    pass
            return [i.strip().lower() for i in v_str.split(",") if i.strip()]
        elif isinstance(v, (list, tuple, set)):
            return [str(i).strip().lower() for i in v if str(i).strip()]
        return ["es", "en"]

    @property
    def brightdata_token(self) -> str:
        """Return configured Bright Data token (checking both BRIGHTDATA_API_TOKEN and BRIGHTDATA_API_KEY)."""
        return (self.BRIGHTDATA_API_TOKEN or self.BRIGHTDATA_API_KEY).strip()

    @property
    def apify_token(self) -> str:
        """Return configured Apify token (checking both APIFY_API_TOKEN and APIFY_API_KEY)."""
        return (self.APIFY_API_TOKEN or self.APIFY_API_KEY).strip()


@lru_cache
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
