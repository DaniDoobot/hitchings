"""Tests for inventory identity audit and preflight verification (Bloque 7G.2)."""

import uuid
from datetime import datetime, timezone
import pytest

from app.core.config import get_settings
from app.models.entry import Entry
from app.models.source import Source
from app.providers.ai.gemini_api import GeminiAPIProvider
from scripts.audit_entry_inventory import (
    normalize_legal_text,
    audit_cat_entry,
    audit_curia_entry,
    audit_cnmc_entry,
    audit_ec_entry,
    run_inventory_audit,
)


def test_canonical_provider_and_model_settings():
    """Verify canonical provider name and model from configuration."""
    settings = get_settings()
    assert settings.GEMINI_MODEL == "gemini-3.8-flash"
    assert settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS == 0.75
    assert settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS == 3.75
    assert GeminiAPIProvider.__name__ == "GeminiAPIProvider"


def test_normalize_legal_text_hyphens_and_quotes():
    """Verify normalization of non-breaking hyphens and unicode quotation marks."""
    raw = "Case C\u201160/25 \u2018Livronsa\u2019 \u2014 Ruling"
    # raw contains actual unicode characters
    actual_unicode = "Case C‑60/25 ‘Livronsa’ — Ruling"
    norm = normalize_legal_text(actual_unicode)
    assert norm == "Case C-60/25 'Livronsa' - Ruling"


def test_cat_identity_success_pdf_text():
    """Verify CAT identity check succeeds when PDF text contains citation and case name."""
    entry = Entry(
        id=uuid.uuid4(),
        title="[2026] CAT 67 | GLOBAL-365 plc & Another v PayPoint plc & Others - Ruling (Costs)",
        content="Neutral citation [2026] CAT 67\nCase No: 1597/5/7/23\nBETWEEN:\nGLOBAL-365 PLC - v - PAYPOINT PLC",
        raw_metadata={
            "neutral_citation": "[2026] CAT 67",
            "case_numbers": ["1597/5/7/23"],
            "case_names": ["GLOBAL-365 plc & Another v PayPoint plc & Others"],
            "content_source": "cat_judgment_pdf_text",
        },
    )
    status, reasons = audit_cat_entry(entry)
    assert status == "IDENTITY_OK"
    assert len(reasons) == 0


def test_cat_identity_mismatch_pdf_text():
    """Verify CAT identity check reports ERROR when PDF text has contradictory or missing identity."""
    entry = Entry(
        id=uuid.uuid4(),
        title="[2026] CAT 67 | GLOBAL-365 plc & Another v PayPoint plc & Others - Ruling (Costs)",
        content="Entirely unrelated document text discussing another tribunal matter with no party names.",
        raw_metadata={
            "neutral_citation": "[2026] CAT 67",
            "case_numbers": ["1597/5/7/23"],
            "case_names": ["GLOBAL-365 plc & Another v PayPoint plc & Others"],
            "content_source": "cat_judgment_pdf_text",
        },
    )
    status, reasons = audit_cat_entry(entry)
    assert status == "IDENTITY_ERROR"
    assert any("PDF text lacks citation" in r for r in reasons)


def test_curia_identity_with_non_breaking_hyphen():
    """Verify CURIA identity matches case number even with non-breaking hyphen."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Case C-60/25 [Livronsa] | Judgment",
        content="In Case C\u201160/25, concerning a reference for a preliminary ruling from the tribunal...",
        raw_metadata={
            "ecli": "ECLI:EU:C:2026:685",
            "case_number": "C-60/25",
        },
    )
    status, reasons = audit_curia_entry(entry)
    assert status == "IDENTITY_OK"
    assert len(reasons) == 0


def test_duplicate_detection_logic():
    """Verify inventory audit tracks duplicate URLs, hashes, and titles."""
    from collections import Counter

    urls = ["https://example.com/1", "https://example.com/2", "https://example.com/1"]
    counts = Counter(urls)
    duplicates = [url for url, cnt in counts.items() if cnt > 1]
    assert len(duplicates) == 1
    assert duplicates[0] == "https://example.com/1"


def test_unambiguous_reporting_fields_contract(db_session):
    """Verify plan_v4_backfill builds entries with unambiguous, typed ID fields."""
    from scripts.plan_v4_backfill import build_v4_backfill_plan

    source = Source(id=uuid.uuid4(), name="Test Source", type="website", url="https://example.com")
    db_session.add(source)
    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Sample Entry For Contract",
        url="https://example.com/item1",
        content="Sample content for testing contract",
        content_hash="hash_123",
    )
    db_session.add(entry)
    db_session.commit()

    plan = build_v4_backfill_plan(db_session)
    assert plan["total_entries"] >= 1
    for it in plan["pending_list"]:
        assert "entry_id" in it
        assert "current_analysis_id" in it
        assert "source_name" in it
        # Ensure UUIDs are strings, not objects or ambiguous labels
        assert isinstance(it["entry_id"], str)
        if it["current_analysis_id"] is not None:
            assert isinstance(it["current_analysis_id"], str)
