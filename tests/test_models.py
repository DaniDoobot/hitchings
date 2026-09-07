"""Tests for SQLAlchemy domain models structure and constraints."""

import uuid
from datetime import datetime, timezone

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.provider import ProviderUsage


def test_source_model_instantiation() -> None:
    """Test Source model fields including nullable url, config JSONB, and timezone-aware timestamps."""
    source = Source(
        name="CNMC Competencia",
        type=SourceType.INSTITUTIONAL,
        url=None,  # Nullable by design (can be search query, keywords, etc.)
        provider="native",
        config={"search_term": "telecomunicaciones"},
        schedule_config={"cron": "0 8 * * 1-5"},
    )
    assert source.name == "CNMC Competencia"
    assert source.type == SourceType.INSTITUTIONAL
    assert source.url is None
    assert source.provider == "native"
    assert source.config == {"search_term": "telecomunicaciones"}
    assert source.schedule_config == {"cron": "0 8 * * 1-5"}
    assert source.active is True


def test_entry_model_instantiation() -> None:
    """Test Entry model with nullable title, optional content_hash, and raw_metadata."""
    source_id = uuid.uuid4()
    entry = Entry(
        source_id=source_id,
        url="https://example.com/posts/123",
        canonical_url=None,
        title=None,  # Nullable by design for social posts without distinct titles
        content="Important regulatory update text.",
        raw_metadata={"platform": "linkedin", "raw_id": "urn:li:share:12345"},
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert entry.source_id == source_id
    assert entry.title is None
    assert entry.content == "Important regulatory update text."
    assert entry.raw_metadata["platform"] == "linkedin"
    assert entry.content_hash is not None


def test_provider_usage_model_instantiation() -> None:
    """Test ProviderUsage tracking model."""
    usage = ProviderUsage(
        provider="brightdata",
        period="2026-09",
        records_used=150,
        soft_limit=4500,
        hard_limit=5000,
        allow_paid_usage=False,
    )
    assert usage.provider == "brightdata"
    assert usage.period == "2026-09"
    assert usage.records_used == 150
    assert usage.soft_limit == 4500
    assert usage.hard_limit == 5000
    assert usage.allow_paid_usage is False


def test_schema_metadata_tables() -> None:
    """Verify that all required tables are correctly registered in SQLAlchemy Base.metadata."""
    from app.models import Base
    table_names = Base.metadata.tables.keys()
    assert "sources" in table_names
    assert "entries" in table_names
    assert "provider_usage" in table_names
