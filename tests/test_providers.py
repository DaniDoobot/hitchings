"""Tests for provider contracts and Free-First policy enforcement."""

import pytest
from app.models.source import Source, SourceType
from app.providers import (
    NativeProvider,
    BrightDataProvider,
    ApifyProvider,
    ProviderDisabledError,
)


@pytest.mark.asyncio
async def test_native_provider_contract() -> None:
    """Test that NativeProvider handles supported source types and provides async fetch interface."""
    provider = NativeProvider()
    source = Source(
        name="Tech Blog",
        type=SourceType.BLOG,
        url="https://techblog.example.com",
        provider="native"
    )
    assert provider.can_handle(source) is True
    assert provider.is_enabled() is True
    assert provider.check_limit_available() is True

    # Async fetch entries stub in Block 0 returns empty list
    entries = await provider.fetch_entries(source)
    assert entries == []


@pytest.mark.asyncio
async def test_brightdata_provider_disabled_by_default() -> None:
    """Test that Bright Data provider is disabled by default in Block 0 and rejects execution."""
    provider = BrightDataProvider()
    source = Source(
        name="LinkedIn Target Company",
        type=SourceType.LINKEDIN_COMPANY,
        url="https://linkedin.com/company/example",
        provider="brightdata"
    )
    assert provider.can_handle(source) is True
    # By default in Block 0:
    assert provider.is_enabled() is False
    assert provider.check_limit_available() is False

    with pytest.raises(ProviderDisabledError) as exc_info:
        await provider.fetch_entries(source)
    assert "disabled" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_apify_provider_disabled_by_default() -> None:
    """Test that Apify provider fallback is disabled by default in Block 0."""
    provider = ApifyProvider()
    source = Source(
        name="Fallback Source",
        type=SourceType.WEBSITE,
        url="https://example.com",
        provider="apify"
    )
    assert provider.can_handle(source) is True
    assert provider.is_enabled() is False

    with pytest.raises(ProviderDisabledError):
        await provider.fetch_entries(source)
