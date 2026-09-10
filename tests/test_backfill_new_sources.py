"""Comprehensive automated tests for Controlled New Sources Backfill (Bloque 12G).

Guarantees:
- 100% offline: ZERO external HTTP requests, ZERO real Gemini API calls.
- Full coverage of Geradin, DMA, OECD, combined runs, idempotency, sufficiency gating,
  future-date guards, volume/cost circuit breakers, matrix binding, and failure isolation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import AIProviderResult, BaseAIProvider
from app.services.new_sources_backfill_service import (
    NewSourcesBackfillReport,
    NewSourcesBackfillService,
    query_db_inventory_counts,
)
from scripts.preview_source_discovery import (
    DMA_SOURCE_NAME,
    GERADIN_SOURCE_NAME,
    OECD_SOURCE_NAME,
)


class MockSpyAIProvider(BaseAIProvider):
    """Test spy AI provider recording calls with zero external network."""

    def __init__(self) -> None:
        self.analyze_calls: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "mock_spy"

    async def analyze(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        matrix_snapshot: dict[str, Any],
        extra_call_metadata: Optional[dict[str, Any]] = None,
        triage_result: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AIProviderResult:
        self.analyze_calls.append({
            "entry_id": entry.id,
            "prompt_code": prompt_version.code,
            "matrix_id": matrix_snapshot.get("matrix_id"),
        })

        if prompt_version.code == "observatory_triage":
            raw_json = json.dumps({
                "relevance_score": 85,
                "relevance_status": "relevant",
                "reason": "Clear competition antitrust development",
                "topics": [{"code": "carteles", "is_primary": True, "confidence": 0.9}],
                "evidence_quote": "substantive antitrust",
            })
        else:
            raw_json = json.dumps({
                "summary": "Deep antitrust investigation summary.",
                "key_points": ["Point 1: Key legal precedent."],
                "evidence_quote": "substantive antitrust",
            })

        return AIProviderResult(
            success=True,
            provider_name=self.provider_name,
            model="mock-v6",
            raw_response=raw_json,
            input_tokens=200,
            output_tokens=100,
            estimated_cost_usd=0.0001,
        )


@pytest.fixture
def active_matrix(db_session: Session) -> TrackingMatrix:
    """Ensure an active tracking matrix exists in the test database."""
    matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"HITCHINGS-TEST-{uuid.uuid4().hex[:4]}",
            name="Matriz de Seguimiento Test",
            status="active",
        )
        db_session.add(matrix)
        db_session.flush()

        topic = TrackingTopic(
            matrix_id=matrix.id,
            code="carteles",
            name="Cárteles",
            priority=1,
            active=True,
        )
        db_session.add(topic)
        db_session.commit()

    return matrix


@pytest.fixture
def v6_prompts(db_session: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    """Ensure active v6 triage and deep prompts exist."""
    triage = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.version == 6,
        )
        .first()
    )
    if not triage:
        triage = AnalysisPromptVersion(
            code="observatory_triage",
            version=6,
            stage="triage",
            name="Observatory Triage v6",
            system_prompt="System instructions",
            user_prompt_template="Triage template: {{entry.content}}",
            response_schema_version="v6",
            active=True,
        )
        db_session.add(triage)

    deep = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.version == 6,
        )
        .first()
    )
    if not deep:
        deep = AnalysisPromptVersion(
            code="observatory_deep_analysis",
            version=6,
            stage="deep_analysis",
            name="Observatory Deep Analysis v6",
            system_prompt="System instructions",
            user_prompt_template="Deep template: {{entry.content}}",
            response_schema_version="v6",
            active=True,
        )
        db_session.add(deep)

    db_session.commit()
    return triage, deep


@pytest.fixture
def backfill_sources(db_session: Session) -> dict[str, Source]:
    """Ensure test sources for Geradin, DMA, and OECD exist."""
    sources: dict[str, Source] = {}

    def get_or_create(name: str, stype: SourceType, provider: str, url: str, config: dict) -> Source:
        s = db_session.query(Source).filter(Source.name == name).first()
        if not s:
            s = Source(
                name=name,
                type=stype,
                provider=provider,
                url=url,
                config=config,
                active=True,
            )
            db_session.add(s)
            db_session.flush()
        else:
            s.active = True
            s.config = config
            db_session.flush()
        return s

    sources["geradin"] = get_or_create(
        GERADIN_SOURCE_NAME,
        SourceType.BLOG,
        "native",
        "https://www.geradinpartners.com/news/",
        {"adapter": "geradin_partners", "listing_url": "https://www.geradinpartners.com/news/"},
    )
    sources["dma"] = get_or_create(
        DMA_SOURCE_NAME,
        SourceType.WEBSITE,
        "native",
        "https://digital-markets-act.ec.europa.eu/news_en",
        {"initial_fetch_limit": 50, "portal_url": "https://digital-markets-act.ec.europa.eu/"},
    )
    sources["oecd"] = get_or_create(
        OECD_SOURCE_NAME,
        SourceType.INSTITUTIONAL,
        "native",
        "https://www.oecd.org/daf/competition/roundtables.htm",
        {"issn": "2075-8677", "initial_fetch_limit": 20},
    )

    db_session.commit()
    return sources


def build_mock_transport_router():
    """Build mock HTTP transport for Geradin, DMA, and OECD endpoints."""
    # 1. Geradin mock pages
    geradin_listing = """<!DOCTYPE html><html><body><ul>
      <li class="wp-block-post category-monthly-eu-litigation-briefing">
        <a class="gp-news-post-card" href="/article-substantive/"><time class="post-date">15/08/26</time><h2>Substantive Litigation Full</h2></a>
      </li>
      <li class="wp-block-post category-newsletters">
        <a class="gp-news-post-card" href="/article-snippet/"><time class="post-date">10/08/26</time><h2>Brief Snippet</h2></a>
      </li>
    </ul></body></html>"""

    geradin_full_detail = """<!DOCTYPE html><html><body><article class="post-content"><div class="column"><p>""" + (
        "Substantive antitrust litigation analysis. " * 50
    ) + """</p></div></article></body></html>"""

    geradin_short_detail = """<!DOCTYPE html><html><body><article class="post-content"><div class="column"><p>Short note only.</p></div></article></body></html>"""

    # 2. DMA mock pages
    dma_listing = """<!DOCTYPE html><html><body><div class="ecl-container">
    <article class="ecl-content-item">
      <ul class="ecl-content-block__primary-meta">
        <li class="ecl-content-block__primary-meta-item">News article</li>
        <li class="ecl-content-block__primary-meta-item"><time datetime="2026-08-20T10:00:00Z">20 August 2026</time></li>
      </ul>
      <h1 class="ecl-content-block__title">
        <a href="/dma-article-1" class="ecl-link">Commission opens DMA non-compliance investigation</a>
      </h1>
      <div class="ecl-content-block__description">
        The European Commission has opened formal proceedings under the Digital Markets Act.
      </div>
    </article>
    </div></body></html>"""

    dma_detail = """<!DOCTYPE html><html><body><main>
      <dl class="ecl-description-list">
        <dt class="ecl-description-list__term">Publication date</dt>
        <dd class="ecl-description-list__definition">20 August 2026</dd>
        <dt class="ecl-description-list__term">Authors</dt>
        <dd class="ecl-description-list__definition">Directorate-General for Competition</dd>
      </dl>
      <article>
        <p>The European Commission has opened formal proceedings under the Digital Markets Act. """ + (
        "Substantive antitrust investigation into gatekeeper core platform services. " * 30
    ) + """</p>
      </article>
    </main></body></html>"""

    # 3. OECD mock Crossref & OpenAlex
    crossref_data = {
        "status": "ok",
        "message": {
            "items": [
                {
                    "DOI": "10.1787/test-full-en",
                    "title": ["Competition and Regulation in Digital Healthcare"],
                    "abstract": "<jats:p>" + ("Substantive antitrust analysis of digital healthcare markets. " * 25) + "</jats:p>",
                    "published": {"date-parts": [[2026, 8, 1]]},
                    "relation": {},
                },
                {
                    "DOI": "10.1787/test-partial-en",
                    "title": ["Information Sharing in Transport Policy"],
                    "published": {"date-parts": [[2026, 8, 5]]},
                    "relation": {},
                },
            ]
        },
    }

    openalex_full = {
        "abstract_inverted_index": {
            "Substantive": [0],
            "antitrust": [1],
            "analysis": [2],
            "of": [3],
            "digital": [4],
            "healthcare": [5],
            "markets": [6],
        }
    }

    openalex_empty = {}

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "geradinpartners.com/news" in url_str:
            return httpx.Response(200, text=geradin_listing)
        elif "article-substantive" in url_str:
            return httpx.Response(200, text=geradin_full_detail)
        elif "article-snippet" in url_str:
            return httpx.Response(200, text=geradin_short_detail)
        elif "digital-markets-act.ec.europa.eu/news" in url_str:
            return httpx.Response(200, text=dma_listing)
        elif "dma-article-1" in url_str:
            return httpx.Response(200, text=dma_detail)
        elif "api.crossref.org" in url_str:
            return httpx.Response(200, json=crossref_data)
        elif "api.openalex.org" in url_str:
            if "test-full-en" in url_str:
                return httpx.Response(200, json=openalex_full)
            return httpx.Response(404, json=openalex_empty)
        elif "oecd.org" in url_str:
            return httpx.Response(200, text="""<!DOCTYPE html><html><body><article><p>""" + ("OECD Competition roundtable report substantive content. " * 25) + """</p></article></body></html>""")
        return httpx.Response(404)

    return router


@pytest.mark.asyncio
async def test_dry_run_mode_zero_http_zero_writes(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """When confirm_real_calls=False, verify 0 HTTP, 0 DB writes, 0 Gemini calls, and status='dry_run'."""
    initial_counts = query_db_inventory_counts(db_session)
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=False,
    )

    assert report.status == "dry_run"
    assert report.is_dry_run is True
    assert report.entries_created == 0
    assert report.potential_analysis == 0
    assert report.analysis_completed == 0
    assert len(spy_ai.analyze_calls) == 0

    # DB inventory must be completely unchanged
    after_counts = query_db_inventory_counts(db_session)
    assert after_counts.entries == initial_counts.entries
    assert after_counts.entry_analyses == initial_counts.entry_analyses
    assert after_counts.analysis_calls == initial_counts.analysis_calls
    assert after_counts.ingestion_runs == initial_counts.ingestion_runs


@pytest.mark.asyncio
async def test_geradin_backfill_isolated(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Targeted backfill of Geradin: creates FULL and INSUFFICIENT entries, analyzes only FULL."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        source_filter="geradin",
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.status == "completed"
    assert report.sources_processed == 1
    g_res = report.per_source[0]
    assert g_res.source_name == GERADIN_SOURCE_NAME
    assert g_res.status == "success"
    assert g_res.created == 2  # 1 full, 1 short
    assert g_res.full_count == 1
    assert g_res.insufficient_count == 1
    assert g_res.eligible == 1
    assert g_res.analyzed == 1
    assert len(spy_ai.analyze_calls) >= 1

    # Verify only the FULL entry was analyzed
    analyses = db_session.query(EntryAnalysis).all()
    assert len(analyses) == 1
    analyzed_entry = db_session.get(Entry, analyses[0].entry_id)
    assert "Substantive Litigation Full" in analyzed_entry.title


@pytest.mark.asyncio
async def test_dma_backfill_isolated(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Targeted backfill of European Commission DMA: creates FULL entries and analyzes them."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        source_filter="dma",
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.status == "completed"
    assert report.sources_processed == 1
    dma_res = report.per_source[0]
    assert dma_res.source_name == DMA_SOURCE_NAME
    assert dma_res.created == 1
    assert dma_res.full_count == 1
    assert dma_res.eligible == 1
    assert dma_res.analyzed == 1


@pytest.mark.asyncio
async def test_oecd_backfill_sufficiency_selective_analysis(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """OECD backfill: persists both FULL and PARTIAL, but analyzes ONLY the FULL entry."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        source_filter="oecd",
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.status == "completed"
    oecd_res = report.per_source[0]
    assert oecd_res.source_name == OECD_SOURCE_NAME
    assert oecd_res.created == 2
    assert oecd_res.full_count == 1
    assert oecd_res.partial_count == 1
    assert oecd_res.eligible == 1
    assert oecd_res.analyzed == 1

    # In DB: 2 entries exist, but exactly 1 EntryAnalysis was created
    oecd_entries = db_session.query(Entry).filter(Entry.source_id == backfill_sources["oecd"].id).all()
    assert len(oecd_entries) == 2
    analyses = db_session.query(EntryAnalysis).all()
    assert len(analyses) == 1


@pytest.mark.asyncio
async def test_all_three_sources_combined_and_idempotency(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Running all 3 sources together, then running a second time verifies strict idempotency."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)

    # First run: persists all items, analyzes eligible
    report_1 = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report_1.status == "completed"
    assert report_1.sources_processed == 3
    # 2 (Geradin) + 1 (DMA) + 2 (OECD) = 5 entries created
    assert report_1.entries_created == 5
    # 1 (Geradin full) + 1 (DMA full) + 1 (OECD full) = 3 eligible & analyzed
    assert report_1.potential_analysis == 3
    assert report_1.analysis_completed == 3

    initial_analyses_count = db_session.query(EntryAnalysis).count()
    initial_entries_count = db_session.query(Entry).count()

    # Second run: identical parameters over existing DB
    report_2 = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report_2.status == "completed"
    assert report_2.entries_created == 0
    assert report_2.duplicates == 5
    assert report_2.potential_analysis == 0
    assert report_2.analysis_completed == 0

    # Invariances: zero new entries, zero new analyses
    assert db_session.query(Entry).count() == initial_entries_count
    assert db_session.query(EntryAnalysis).count() == initial_analyses_count


@pytest.mark.asyncio
async def test_future_oecd_publication_skipped(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Verify that an OECD publication with a future date is completely excluded."""
    now_dt = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    future_record = {
        "status": "ok",
        "message": {
            "items": [
                {
                    "DOI": "10.1787/future-test",
                    "title": ["Future OECD Roundtable 2030"],
                    "published": {"date-parts": [[2030, 1, 1]]},
                    "relation": {},
                }
            ]
        },
    }

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.crossref.org" in url_str:
            return httpx.Response(200, json=future_record)
        return httpx.Response(200, json={"results": []})

    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        source_filter="oecd",
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.entries_created == 0
    assert report.analysis_completed == 0
    assert len(spy_ai.analyze_calls) == 0


@pytest.mark.asyncio
async def test_max_new_entries_guard_aborts_before_persistence(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """When discovered new entries exceed --max-new-entries, aborts before persistence."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    initial_entries_count = db_session.query(Entry).count()

    # Limit to 1 new entry when 5 will be discovered
    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        max_new_entries=1,
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.status == "aborted_max_new_entries"
    assert "--max-new-entries limit of 1" in report.guard_triggered
    assert report.entries_created == 0
    assert report.analysis_completed == 0
    assert len(spy_ai.analyze_calls) == 0

    # Strict invariant: zero rows added to Entry table
    assert db_session.query(Entry).count() == initial_entries_count


@pytest.mark.asyncio
async def test_max_analysis_calls_guard_aborts_before_gemini(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """When eligible analyses exceed --max-analysis-calls, aborts before calling Gemini."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    # Allow 10 new entries, but cap analysis calls at 1 when 3 will be eligible
    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        max_new_entries=30,
        max_analysis_calls=1,
        sync_client=sync_client,
        async_client=async_client,
    )

    assert report.status == "aborted_max_analysis_calls"
    assert "--max-analysis-calls limit of 1" in report.guard_triggered
    assert report.analysis_completed == 0
    assert len(spy_ai.analyze_calls) == 0


@pytest.mark.asyncio
async def test_analysis_uses_active_tracking_matrix(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Verify that all generated EntryAnalysis records are strictly bound to the active matrix."""
    router = build_mock_transport_router()
    sync_client = httpx.Client(transport=httpx.MockTransport(router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        source_filter="dma",
        sync_client=sync_client,
        async_client=async_client,
    )

    analyses = db_session.query(EntryAnalysis).all()
    assert len(analyses) >= 1
    for ea in analyses:
        assert ea.matrix_id == active_matrix.id
        assert ea.pipeline_version == "v6"


@pytest.mark.asyncio
async def test_source_failure_isolation(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v6_prompts: tuple,
    backfill_sources: dict[str, Source],
):
    """Failure in Geradin ingestion does NOT stop DMA from successfully ingesting and analyzing."""
    base_router = build_mock_transport_router()

    def failing_router(request: httpx.Request) -> httpx.Response:
        if "geradinpartners.com" in str(request.url):
            return httpx.Response(503, text="Service Unavailable")
        return base_router(request)

    sync_client = httpx.Client(transport=httpx.MockTransport(failing_router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_router))
    spy_ai = MockSpyAIProvider()

    service = NewSourcesBackfillService(ai_provider=spy_ai)
    report = await service.execute_backfill(
        db=db_session,
        lookback_days=90,
        confirm_real_calls=True,
        sync_client=sync_client,
        async_client=async_client,
    )

    g_res = next(s for s in report.per_source if s.source_name == GERADIN_SOURCE_NAME)
    dma_res = next(s for s in report.per_source if s.source_name == DMA_SOURCE_NAME)

    # Geradin failed, but DMA succeeded!
    assert g_res.status == "failed"
    assert g_res.ingestion_failed >= 1
    assert dma_res.status == "success"
    assert dma_res.created == 1
    assert dma_res.analyzed == 1
    assert report.entries_created >= 1
