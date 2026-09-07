"""Tests for Competition Appeal Tribunal (CAT) judgments extractor, summary parser, and ingestion."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRunStatus
from app.models.tracking import TrackedEntity
from app.providers.native import NativeProvider
from app.providers.extractors.competition_appeal_tribunal import (
    CompetitionAppealTribunalExtractor,
    slugify_citation,
    parse_case_link_text,
)
from app.services.ingestion_service import IngestionService

SAMPLE_CAT_LISTING_HTML = """<!DOCTYPE html>
<html>
<head><title>Judgments | Competition Appeal Tribunal</title></head>
<body>
<div class="view-judgments">
  <div class="view-content">
    <ul>
      <!-- Row 1: Multi-case with summary and [2026] CAT 71 -->
      <li class="views-row">
        <div>
          <div class="readmore-group">
            <div class="h4 | margin-top-none">
              <a href="/cases/12667716-walter-hugh-merricks-cbe">1266/7/7/16 Walter Hugh Merricks CBE v Mastercard Incorporated and Others</a>
            </div>
            <div class="h4 | margin-top-none">
              <a href="/cases/14417722-cicc-i">1441/7/7/22 Commercial and Interregional Card Claims I Limited v Mastercard Incorporated</a>
            </div>
            <div class="readmore-inner">
              <div class="h4 | margin-top-none">
                <a href="/cases/151711722-um-merchant">1517/11/7/22 (UM) Merchant Interchange Fee Umbrella Proceedings</a>
              </div>
            </div>
            <p><a class="readmore-toggle" href="node-9880"><span>See all</span> the cases</a></p>
          </div>
          <h2 class="h5 | margin-compact">
            <span>
              <span class="file file--mime-application-pdf">
                <a class="link-plain" href="/sites/cat/files/2026-08/151711722-ruling-trial-2-costs.pdf">
                  Ruling (Trial 2 Costs)
                </a>
              </span>
            </span>
          </h2>
          <span><time datetime="2026-08-21T12:00:00Z">21/08/2026</time></span>
          <span>[2026]</span>
          <span>CAT</span>
          <span>71</span>
          <span class="link-summary"> | <a href="/judgments/151711722-um-merchant-ruling-trial-2-costs-21-aug">Summary</a></span>
        </div>
      </li>

      <!-- Row 2: Single-case with summary and [2026] CAT 70 -->
      <li class="views-row">
        <div>
          <div class="readmore-group">
            <div class="h4 | margin-top-none">
              <a href="/cases/17887726-bed-and-breakfast">1788/7/7/26 Bed and Breakfast Association Limited v Booking Holdings Inc.</a>
            </div>
          </div>
          <h2 class="h5 | margin-compact">
            <span>
              <span class="file file--mime-application-pdf">
                <a class="link-plain" href="/sites/cat/files/2026-08/17887726-ruling-chair-service-out.pdf">
                  Ruling of the Chair (Service Out)
                </a>
              </span>
            </span>
          </h2>
          <span><time datetime="2026-08-18T12:00:00Z">18/08/2026</time></span>
          <span>[2026]</span>
          <span>CAT</span>
          <span>70</span>
          <span class="link-summary"> | <a href="/judgments/17887726-bed-and-breakfast-ruling-service-out">Summary</a></span>
        </div>
      </li>

      <!-- Row 3: Court of Appeal decision WITHOUT summary link and [2026] EWCA Civ 993 -->
      <li class="views-row">
        <div>
          <div class="readmore-group">
            <div class="h4 | margin-top-none">
              <a href="/cases/14337722-dr-liza-lovdahl-gormsen">1433/7/7/22 Dr Liza Lovdahl Gormsen v Meta Platforms, Inc. and Others</a>
            </div>
          </div>
          <h2 class="h5 | margin-compact">
            <span>
              <span class="file file--mime-application-pdf">
                <a class="link-plain" href="/sites/cat/files/2026-07/14337722-judgment-court-appeal.pdf">
                  Judgment of the Court of Appeal (Pleading Amendments)
                </a>
              </span>
            </span>
          </h2>
          <span><time datetime="2026-07-29T12:00:00Z">29/07/2026</time></span>
          <span>[2026]</span>
          <span>EWCA Civ</span>
          <span>993</span>
        </div>
      </li>
    </ul>
  </div>
</div>
</body>
</html>
"""

SAMPLE_CAT_SUMMARY_HTML = """<!DOCTYPE html>
<html>
<head><title>Summary of Ruling (Trial 2 Costs) | Competition Appeal Tribunal</title></head>
<body>
<main>
  <div class="container">
    <h2 class="h4">Summary</h2>
    <div class="above-related">
      <p>Ruling concerning costs arising from the judgment of the Tribunal dated 18 February 2026 [2026] CAT 11.</p>
      <p>The Chair ruled that the Merchant Claimants reasonable and proportionate costs recoverable from the Defendants in respect of Trial 2A should be reduced by 5%.</p>
      <p>The Chair further ruled that the costs of expert evidence were not proportionately incurred.</p>
    </div>
  </div>
</main>
</body>
</html>
"""


# ==============================================================================
# 1. PARSING & EXTRACTION UNIT TESTS
# ==============================================================================

def test_cat_slugify_citation_and_case_link_parsing() -> None:
    """Test helper functions for citation anchors and case link splitting."""
    assert slugify_citation("[2026] CAT 71") == "2026-cat-71"
    assert slugify_citation("[2026] EWCA Civ 993") == "2026-ewca-civ-993"

    num, name = parse_case_link_text("1407/7/7/21 (T) Commercial and Inter-Dealer Telecommunications Limited v Ofcom")
    assert num == "1407/7/7/21 (T)"
    assert name == "Commercial and Inter-Dealer Telecommunications Limited v Ofcom"

    num2, name2 = parse_case_link_text("1266/7/7/16 Walter Hugh Merricks CBE v Mastercard Incorporated")
    assert num2 == "1266/7/7/16"
    assert name2 == "Walter Hugh Merricks CBE v Mastercard Incorporated"

    num3, name3 = parse_case_link_text("Plain Case Name Without Number")
    assert num3 == ""
    assert name3 == "Plain Case Name Without Number"


def test_cat_parse_listing_page_structure() -> None:
    """Test parsing CAT judgments views-rows: multi-cases, citations, summaries, and dates."""
    extractor = CompetitionAppealTribunalExtractor()
    items = extractor.parse_listing_page(SAMPLE_CAT_LISTING_HTML, "https://www.catribunal.org.uk/judgments")

    assert len(items) == 3

    # Row 1: Multi-case with summary
    it1 = items[0]
    assert "[2026] CAT 71" in it1["title"]
    assert "Walter Hugh Merricks" in it1["title"]
    assert "Ruling (Trial 2 Costs)" in it1["title"]
    assert it1["published_at"] == datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
    assert it1["summary_url"] == "https://www.catribunal.org.uk/judgments/151711722-um-merchant-ruling-trial-2-costs-21-aug"
    assert it1["entry_url"] == it1["summary_url"]

    meta1 = it1["raw_metadata"]
    assert meta1["neutral_citation"] == "[2026] CAT 71"
    assert meta1["decision_type"] == "Ruling (Trial 2 Costs)"
    assert meta1["has_summary"] is True
    assert len(meta1["case_numbers"]) == 3
    assert "1266/7/7/16" in meta1["case_numbers"]
    assert "1517/11/7/22 (UM)" in meta1["case_numbers"]
    assert len(meta1["case_names"]) == 3
    assert len(meta1["case_urls"]) == 3
    assert meta1["judgment_pdf_url"] == "https://www.catribunal.org.uk/sites/cat/files/2026-08/151711722-ruling-trial-2-costs.pdf"

    # Row 2: Single-case with summary
    it2 = items[1]
    assert "[2026] CAT 70" in it2["title"]
    assert "Bed and Breakfast Association Limited" in it2["title"]
    assert it2["published_at"] == datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
    assert it2["raw_metadata"]["neutral_citation"] == "[2026] CAT 70"
    assert it2["raw_metadata"]["has_summary"] is True

    # Row 3: Court of Appeal decision without summary
    it3 = items[2]
    assert "[2026] EWCA Civ 993" in it3["title"]
    assert "Dr Liza Lovdahl Gormsen" in it3["title"]
    assert it3["published_at"] == datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert it3["summary_url"] is None
    assert it3["raw_metadata"]["has_summary"] is False
    assert it3["raw_metadata"]["neutral_citation"] == "[2026] EWCA Civ 993"
    assert "https://www.catribunal.org.uk/cases/14337722-dr-liza-lovdahl-gormsen#2026-ewca-civ-993" == it3["entry_url"]
    assert it3["external_id"] == "[2026] EWCA Civ 993"


def test_cat_parse_judgment_summary() -> None:
    """Test extracting clean summary text and excerpt from judgment detail page."""
    extractor = CompetitionAppealTribunalExtractor()
    content, excerpt = extractor.parse_judgment_summary(SAMPLE_CAT_SUMMARY_HTML)

    assert content is not None
    assert excerpt is not None
    assert "Ruling concerning costs arising from the judgment" in content
    assert "expert evidence were not proportionately incurred" in content
    # Excerpt should be first paragraph
    assert "Ruling concerning costs arising" in excerpt
    assert "expert evidence were not proportionately" not in excerpt


def test_cat_parse_judgment_summary_fallback() -> None:
    """Test graceful handling when detail HTML does not contain expected summary markup."""
    extractor = CompetitionAppealTribunalExtractor()
    content, excerpt = extractor.parse_judgment_summary("<html><body><div>Just some unrelated text</div></body></html>")
    assert content is None
    assert excerpt is None


# ==============================================================================
# 2. NATIVE PROVIDER DISPATCH TEST
# ==============================================================================

class MockResponse:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")


async def mock_cat_network_router(url, *args, **kwargs):
    url_str = str(url)
    if "judgments/151711722" in url_str:
        return MockResponse(200, text=SAMPLE_CAT_SUMMARY_HTML)
    elif "judgments/17887726" in url_str:
        return MockResponse(200, text="<main><h2>Summary</h2><div class='above-related'><p>Ruling of the Chair on service out.</p></div></main>")
    elif "page=" in url_str and "page=0" not in url_str:
        # Subsequent pages return empty HTML to terminate pagination
        return MockResponse(200, text="<html><body><div class='view-judgments'></div></body></html>")
    elif "judgments" in url_str:
        return MockResponse(200, text=SAMPLE_CAT_LISTING_HTML)
    return MockResponse(404, text="Not found")


@pytest.mark.asyncio
async def test_native_provider_dispatches_cat() -> None:
    """Test that NativeProvider dispatches to CompetitionAppealTribunalExtractor for catribunal.org.uk."""
    provider = NativeProvider()
    source = Source(
        name="Competition Appeal Tribunal - Judgments",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://www.catribunal.org.uk/judgments",
        category="institutional",
        config={"initial_fetch_limit": 5},
    )

    from unittest.mock import patch
    with patch("httpx.AsyncClient.get", side_effect=mock_cat_network_router):
        entries = await provider.fetch_entries(source)

    assert len(entries) == 3

    # Entry 1: enriched with summary
    e1 = entries[0]
    assert "[2026] CAT 71" in (e1.title or "")
    assert e1.content_type == "judicial_decision"
    assert e1.language == "en"
    assert "Ruling concerning costs arising" in (e1.content or "")
    assert e1.raw_metadata["has_summary"] is True
    assert e1.raw_metadata["judgment_pdf_url"] is not None

    # Entry 3: without summary
    e3 = entries[2]
    assert "[2026] EWCA Civ 993" in (e3.title or "")
    assert e3.content is None
    assert e3.excerpt is None
    assert e3.raw_metadata["has_summary"] is False


# ==============================================================================
# 3. END-TO-END INGESTION SERVICE & DEDUPLICATION TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_cat_ingestion_service_e2e_and_deduplication(db_session: Session) -> None:
    """Test full IngestionService flow with CAT: entry creation, metadata, deduplication, and runs."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="Competition Appeal Tribunal Test",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="Competition Appeal Tribunal - Judgments Test",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://www.catribunal.org.uk/judgments",
        active=True,
        category="institutional",
        tracked_entity_id=entity.id,
        config={"initial_fetch_limit": 20, "freshness_warning_hours": 336},
    )
    db_session.add(source)
    db_session.commit()

    service = IngestionService()

    from unittest.mock import patch
    with patch("httpx.AsyncClient.get", side_effect=mock_cat_network_router):
        # First Run: creates 3 entries
        res1 = await service.ingest_source(source.id, db_session)
        assert res1.status == IngestionRunStatus.SUCCESS.value
        assert res1.fetched == 3
        assert res1.created == 3
        assert res1.duplicates == 0

        # Verify entries in database
        entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(entries) == 3

        for entry in entries:
            assert entry.content_type == "judicial_decision"
            assert entry.language == "en"
            assert entry.raw_metadata is not None
            assert "neutral_citation" in entry.raw_metadata
            assert "judgment_pdf_url" in entry.raw_metadata

        # Second Run: exact duplicates, creates 0
        res2 = await service.ingest_source(source.id, db_session)
        assert res2.status == IngestionRunStatus.SUCCESS.value
        assert res2.fetched == 3
        assert res2.created == 0
        assert res2.duplicates == 3

        # Database still has exactly 3 entries
        entries_after = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(entries_after) == 3

        # Verify freshness calculation
        status_resp = service.get_source_status(source, db_session)
        assert status_resp.freshness is not None
        assert status_resp.freshness.warning_hours == 336
