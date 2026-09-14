"""Tests for Bloque 16B.5: Critical lookback propagation across backfill phases and offline reproduction."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import httpx
from sqlalchemy.orm import Session

from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.models.analysis import AnalysisPromptVersion
from app.providers.base import RawEntryData
from app.providers.native import NativeProvider
from app.providers.extractors.autorite_concurrence import (
    AutoriteConcurrenceExtractor,
    ADLC_SOURCE_NAME,
    ADLC_BASE_URL,
)
from app.providers.extractors.cma import CMAExtractor
from app.providers.extractors.bundeskartellamt import BundeskartellamtExtractor
from app.providers.extractors.oecd_competition import OECDCompetitionExtractor
from app.providers.extractors.european_commission_dma import EuropeanCommissionDMAExtractor
from app.providers.ai.mock import MockAIProvider
from app.services.ingestion_service import IngestionService
from app.services.new_sources_backfill_service import NewSourcesBackfillService


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def active_matrix(db_session: Session) -> TrackingMatrix:
    matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"HITCHINGS-TEST-{uuid.uuid4().hex[:4]}",
            name="Matriz Test 16B.5",
            status="active",
        )
        db_session.add(matrix)
        db_session.flush()
        topic = TrackingTopic(
            matrix_id=matrix.id,
            code="merger_control",
            name="Control de Concentraciones",
            priority=1,
            active=True,
        )
        db_session.add(topic)
        db_session.commit()
    return matrix


@pytest.fixture
def v7_prompts(db_session: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    triage = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_triage", AnalysisPromptVersion.version == 7)
        .first()
    )
    if not triage:
        triage = AnalysisPromptVersion(
            code="observatory_triage",
            version=7,
            stage="triage",
            name="Observatory Triage v7",
            system_prompt="System instructions",
            user_prompt_template="Triage template: {{entry.content}}",
            response_schema_version="v3",
            config={"grounding_mode": "evidence_blocks_v1"},
            active=True,
        )
        db_session.add(triage)

    deep = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 7)
        .first()
    )
    if not deep:
        deep = AnalysisPromptVersion(
            code="observatory_deep_analysis",
            version=7,
            stage="deep_analysis",
            name="Observatory Deep Analysis v7",
            system_prompt="System instructions",
            user_prompt_template="Deep template: {{entry.content}}",
            response_schema_version="v3",
            config={"grounding_mode": "evidence_blocks_v1"},
            active=True,
        )
        db_session.add(deep)

    db_session.commit()
    return triage, deep


# ==============================================================================
# 1. Extractor Lookback Propagations (ADLC, CMA, BKart, OECD, DMA)
# ==============================================================================

@pytest.mark.asyncio
async def test_adlc_extractor_respects_explicit_lookback():
    """Verify AutoriteConcurrenceExtractor respects explicit lookback_days."""
    extractor = AutoriteConcurrenceExtractor()
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL)

    with patch.object(extractor, "discover_candidate_urls", new=AsyncMock(return_value=[])) as mock_discover:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await extractor.extract(client=mock_client, source=source, lookback_days=13)
        assert mock_discover.called
        cutoff_used = mock_discover.call_args[1]["cutoff"]
        now = datetime.now(timezone.utc)
        diff_days = (now - cutoff_used).total_seconds() / 86400.0
        assert 12.9 < diff_days < 13.1, f"Expected ~13 days cutoff, got {diff_days}"


@pytest.mark.asyncio
async def test_adlc_extractor_defaults_to_8_days_when_no_lookback():
    """Verify AutoriteConcurrenceExtractor defaults to 8 days when no lookback is provided."""
    extractor = AutoriteConcurrenceExtractor()
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL, config={})

    with patch.object(extractor, "discover_candidate_urls", new=AsyncMock(return_value=[])) as mock_discover:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await extractor.extract(client=mock_client, source=source)
        assert mock_discover.called
        cutoff_used = mock_discover.call_args[1]["cutoff"]
        now = datetime.now(timezone.utc)
        diff_days = (now - cutoff_used).total_seconds() / 86400.0
        assert 7.9 < diff_days < 8.1, f"Expected ~8 days cutoff for default/scheduler, got {diff_days}"


@pytest.mark.asyncio
async def test_cma_extractor_respects_explicit_lookback():
    """Verify CMAExtractor respects explicit lookback_days."""
    extractor = CMAExtractor()
    source = Source(id=uuid.uuid4(), name="CMA", type=SourceType.INSTITUTIONAL, provider="native", url="https://www.gov.uk/cma-cases")

    with patch.object(extractor, "discover_cases", new=AsyncMock(return_value=[])) as mock_discover:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await extractor.extract(client=mock_client, source=source, lookback_days=13)
        assert mock_discover.called
        cutoff_used = mock_discover.call_args[0][1]
        now = datetime.now(timezone.utc)
        diff_days = (now - cutoff_used).total_seconds() / 86400.0
        assert 12.9 < diff_days < 13.1


@pytest.mark.asyncio
async def test_bundeskartellamt_extractor_respects_explicit_lookback():
    """Verify BundeskartellamtExtractor respects explicit lookback_days."""
    extractor = BundeskartellamtExtractor()
    source = Source(id=uuid.uuid4(), name="Bundeskartellamt", type=SourceType.INSTITUTIONAL, provider="native", url="https://www.bundeskartellamt.de")

    with patch.object(extractor, "discover_candidates", new=AsyncMock(return_value=[])) as mock_discover:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await extractor.extract(client=mock_client, source=source, lookback_days=13)
        assert mock_discover.called
        lookback_used = mock_discover.call_args[1]["lookback_days"]
        assert lookback_used == 13


@pytest.mark.asyncio
async def test_native_provider_propagates_lookback_to_extractors():
    """Verify NativeProvider passes lookback_days parameter to extractor.extract."""
    provider = NativeProvider()
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL)

    with patch("app.providers.extractors.autorite_concurrence.AutoriteConcurrenceExtractor.extract", new=AsyncMock(return_value=[])) as mock_ext:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await provider.fetch_entries(source=source, client=mock_client, lookback_days=13)
        assert mock_ext.called
        assert mock_ext.call_args[1]["lookback_days"] == 13


@pytest.mark.asyncio
async def test_ingestion_service_propagates_lookback_to_provider(db_session: Session):
    """Verify IngestionService.ingest_source propagates lookback_days down to provider.fetch_entries."""
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL, active=True)
    db_session.add(source)
    db_session.commit()

    service = IngestionService()
    provider = service.get_provider("native")

    with patch.object(provider, "fetch_entries", new=AsyncMock(return_value=[])) as mock_fetch:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await service.ingest_source(source_id=source.id, db=db_session, client=mock_client, lookback_days=13)
        assert mock_fetch.called
        assert mock_fetch.call_args[1]["lookback_days"] == 13


# ==============================================================================
# 2. Consistent Lookback Propagation Across Backfill (13 days vs 90 days)
# ==============================================================================

@pytest.mark.asyncio
async def test_backfill_consistent_lookback_13_days(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Verify that --lookback-days 13 propagates 13 across pre-discovery, prospective check, and ingestion."""
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL, active=True)
    db_session.add(source)
    db_session.commit()

    recorded_lookbacks = []

    async def spy_extract(self, client, source, lookback_days=None, **kwargs):
        eff = lookback_days or (source.config or {}).get("lookback_days")
        recorded_lookbacks.append(eff)
        return []

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=spy_extract):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=13,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=10,
            async_client=mock_client,
        )

        assert report.status == "completed"
        # Three extraction calls occurred: pre-discovery, prospective check, and real ingestion
        assert len(recorded_lookbacks) >= 3
        assert all(lb == 13 for lb in recorded_lookbacks), f"All phases must use 13 days, got: {recorded_lookbacks}"


@pytest.mark.asyncio
async def test_backfill_consistent_lookback_90_days(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Verify that --lookback-days 90 propagates 90 across all phases."""
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL, active=True)
    db_session.add(source)
    db_session.commit()

    recorded_lookbacks = []

    async def spy_extract(self, client, source, lookback_days=None, **kwargs):
        eff = lookback_days or (source.config or {}).get("lookback_days")
        recorded_lookbacks.append(eff)
        return []

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=spy_extract):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=90,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=10,
            async_client=mock_client,
        )

        assert report.status == "completed"
        assert len(recorded_lookbacks) >= 3
        assert all(lb == 90 for lb in recorded_lookbacks), f"All phases must use 90 days, got: {recorded_lookbacks}"


# ==============================================================================
# 3. Offline Reproduction of the Production Bug: Item from day 12 ingested cleanly
# ==============================================================================

@pytest.mark.asyncio
async def test_offline_reproduction_12_day_old_item_persisted_with_13_day_lookback(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Reproduce exact production bug:
    - 2 existing items published 5 days ago (already in DB -> duplicates).
    - 1 candidate item published 12 days ago (26-DCC-179 -> new).
    - With lookback_days=13:
      Precheck sees 1 new candidate.
      Prospective check sees 1 new candidate.
      Ingestion MUST create the entry (created=1, duplicates=2).
    """
    now = datetime.now(timezone.utc)
    source = Source(id=uuid.uuid4(), name=ADLC_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native", url=ADLC_BASE_URL, active=True)
    db_session.add(source)
    db_session.flush()

    # Seed 2 existing entries in DB (within last 8 days)
    dt_5d = now - timedelta(days=5)
    e1 = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-181",
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-181",
        title="Décision 26-DCC-181",
        content="Contenu substantiel concentration 181.",
        published_at=dt_5d,
        raw_metadata={"official_id": "26-DCC-181"},
    )
    e2 = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-180",
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-180",
        title="Décision 26-DCC-180",
        content="Contenu substantiel concentration 180.",
        published_at=dt_5d,
        raw_metadata={"official_id": "26-DCC-180"},
    )
    db_session.add(e1)
    db_session.add(e2)
    db_session.commit()

    # Mock items returned by extractor when lookback=13:
    # 2 items from 5 days ago, and 1 item from 12 days ago (26-DCC-179)
    dt_12d = now - timedelta(days=12)
    raw_181 = RawEntryData(
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-181",
        title="Décision 26-DCC-181",
        content="Contenu substantiel concentration 181.",
        published_at=dt_5d,
        external_id="adlc:act:26-dcc-181",
        raw_metadata={"official_id": "26-DCC-181"},
    )
    raw_180 = RawEntryData(
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-180",
        title="Décision 26-DCC-180",
        content="Contenu substantiel concentration 180.",
        published_at=dt_5d,
        external_id="adlc:act:26-dcc-180",
        raw_metadata={"official_id": "26-DCC-180"},
    )
    raw_179 = RawEntryData(
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-179",
        title="Décision 26-DCC-179 du 2 septembre 2026",
        content="Contenu substantiel concentration 179 " * 50,  # FULL sufficiency
        published_at=dt_12d,
        external_id="adlc:act:26-dcc-179",
        raw_metadata={"official_id": "26-DCC-179"},
    )

    async def mock_extract(self, client, source, lookback_days=None, **kwargs):
        eff = lookback_days or (source.config or {}).get("lookback_days", 8)
        cutoff = now - timedelta(days=eff)
        items = [raw_181, raw_180, raw_179]
        return [it for it in items if it.published_at >= cutoff]

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=mock_extract):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=13,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=1,
            max_analysis_entries=1,
            max_analysis_calls=2,
            async_client=mock_client,
        )

        assert report.status == "completed"
        # Verification: 26-DCC-179 was created!
        assert report.entries_created == 1, f"Expected 1 entry created, got {report.entries_created}"
        assert report.duplicates == 2, f"Expected 2 duplicates, got {report.duplicates}"

        # In DB: 26-DCC-179 exists
        created_entry = db_session.query(Entry).filter(Entry.external_id == "adlc:act:26-dcc-179").first()
        assert created_entry is not None
        assert created_entry.source_id == source.id
        assert "26-DCC-179" in created_entry.title


# ==============================================================================
# 4. Source.config Immutability (Bloque 16B.5.1: Execution-Scoped Lookback)
# ==============================================================================

@pytest.mark.asyncio
async def test_backfill_does_not_mutate_or_persist_source_config_when_empty(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Bloque 16B.5.1: Verify backfill with lookback_days=90 does NOT persist lookback_days into Source.config in DB."""
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        config={},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=AsyncMock(return_value=[])):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=90,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=10,
            async_client=mock_client,
        )

        assert report.status == "completed"

    # Refresh source directly from the database
    db_session.refresh(source)
    assert source.config is not None
    assert "lookback_days" not in source.config, (
        f"Source.config in DB was poisoned with lookback_days! Found: {source.config}"
    )


@pytest.mark.asyncio
async def test_backfill_preserves_existing_custom_keys_in_source_config(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Bloque 16B.5.1: Verify backfill with lookback_days=90 does not alter existing keys nor add lookback_days."""
    original_config = {"custom_field": "val123", "extra_notes": "adlc notes"}
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        config=dict(original_config),
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=AsyncMock(return_value=[])):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=90,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=10,
            async_client=mock_client,
        )

        assert report.status == "completed"

    db_session.refresh(source)
    assert source.config == original_config
    assert "lookback_days" not in source.config


@pytest.mark.asyncio
async def test_backfill_does_not_overwrite_persistent_lookback_days_in_source_config(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Bloque 16B.5.1: Verify backfill with lookback_days=90 does not overwrite persistent lookback_days=15."""
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        config={"lookback_days": 15},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    with patch.object(AutoriteConcurrenceExtractor, "extract", new=AsyncMock(return_value=[])):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        service = NewSourcesBackfillService(ai_provider=MockAIProvider())
        report = await service.execute_backfill(
            db=db_session,
            lookback_days=90,
            confirm_real_calls=True,
            source_filter="adlc",
            max_new_entries=10,
            async_client=mock_client,
        )

        assert report.status == "completed"

    db_session.refresh(source)
    assert source.config == {"lookback_days": 15}, (
        f"Source.config persistent lookback_days was overwritten! Found: {source.config}"
    )


@pytest.mark.asyncio
async def test_direct_web_ingestion_does_not_mutate_source_config(db_session: Session):
    """Bloque 16B.5.1: DirectWebIngestionService must NOT mutate or persist lookback_days into source.config."""
    from app.services.direct_web_ingestion_service import DirectWebIngestionService
    from app.providers.direct_web.adapters.geradin_partners import GeradinPartnersAdapter

    source = Source(
        id=uuid.uuid4(),
        name="Geradin Partners - EU Competition & Litigation",
        type=SourceType.BLOG,
        provider="direct_web",
        url="https://www.geradinpartners.com",
        config={"listing_url": "https://www.geradinpartners.com/blog/"},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    dw_service = DirectWebIngestionService()
    adapter = GeradinPartnersAdapter()

    with patch.object(adapter, "discover", return_value=[]), \
         patch("app.providers.direct_web.registry.DirectWebAdapterRegistry.get_adapter_for_source", return_value=adapter):
        dw_service.execute_ingestion(
            db=db_session,
            sources=[source],
            confirm_real_calls=True,
            lookback_days=90,
        )

    db_session.refresh(source)
    assert source.config == {"listing_url": "https://www.geradinpartners.com/blog/"}
    assert "lookback_days" not in source.config


@pytest.mark.asyncio
async def test_ingestion_service_does_not_mutate_source_config(db_session: Session):
    """Bloque 16B.5.1: IngestionService must NOT mutate or persist lookback_days into source.config."""
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        config={},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    service = IngestionService()
    provider = service.get_provider("native")

    with patch.object(provider, "fetch_entries", new=AsyncMock(return_value=[])):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        await service.ingest_source(
            source_id=source.id,
            db=db_session,
            client=mock_client,
            lookback_days=90,
        )

    db_session.refresh(source)
    assert source.config == {}
    assert "lookback_days" not in source.config
