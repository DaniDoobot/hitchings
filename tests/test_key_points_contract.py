"""Tests for EntryAnalysis.key_points contract, serialization, and API retrocompatibility (Bloque 7G)."""

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.schemas.analysis import EntryAnalysisResponse, EntryAnalysisDetailResponse


def test_key_points_schema_normalization_from_strings():
    """Test that list[str] key_points remains unchanged in schemas."""
    data = {
        "id": uuid.uuid4(),
        "entry_id": uuid.uuid4(),
        "matrix_id": uuid.uuid4(),
        "pipeline_version": "v2",
        "status": "completed",
        "matrix_snapshot_hash": "dummy_hash",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "key_points": ["Point one", "Point two", "Point three"],
    }
    resp = EntryAnalysisResponse(**data)
    assert resp.key_points == ["Point one", "Point two", "Point three"]


def test_key_points_schema_normalization_from_dicts():
    """Bloque 7G: Test that if key_points contains list[dict] with 'point', schema extracts list[str]."""
    data = {
        "id": uuid.uuid4(),
        "entry_id": uuid.uuid4(),
        "matrix_id": uuid.uuid4(),
        "pipeline_version": "v3",
        "status": "completed",
        "matrix_snapshot_hash": "dummy_hash",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "key_points": [
            {"point": "Structured point 1", "evidence": [{"quote": "quote 1"}]},
            {"point": "Structured point 2", "evidence": [{"quote": "quote 2"}]},
        ],
    }
    resp = EntryAnalysisResponse(**data)
    assert resp.key_points == ["Structured point 1", "Structured point 2"]


def test_api_endpoint_returns_list_of_strings_for_v2_and_v3_dicts(client: TestClient, db_session: Session):
    """Bloque 7G (A & B): GET /api/v1/entry-analyses/{id} always returns key_points: list[str]."""
    src = Source(name="Test Source", type=SourceType.WEBSITE, provider="native", url="https://test.local/")
    db_session.add(src)
    db_session.flush()

    matrix = TrackingMatrix(code="TEST-MATRIX", name="Test Matrix", status="active")
    db_session.add(matrix)
    db_session.flush()

    entry = Entry(
        source_id=src.id,
        title="Test Entry",
        url="https://test.local/1",
        content="Sample content",
        content_hash="test_hash_1",
    )
    db_session.add(entry)
    db_session.flush()

    # 1. Create v2 analysis with list[str]
    ea_v2 = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v2",
        status="completed",
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="hash_v2",
        key_points=["Point A", "Point B"],
        summary="Summary v2",
    )
    db_session.add(ea_v2)

    # 2. Create simulated historical analysis where DB has list[dict]
    ea_v3_dict = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v3",
        status="completed",
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="hash_v3",
        key_points=[
            {"point": "Point from dict 1", "evidence": ["quote 1"]},
            {"point": "Point from dict 2", "evidence": ["quote 2"]},
        ],
        summary="Summary v3",
    )
    db_session.add(ea_v3_dict)
    db_session.commit()

    # Test v2 response
    resp_v2 = client.get(f"/api/v1/entry-analyses/{ea_v2.id}")
    assert resp_v2.status_code == 200
    json_v2 = resp_v2.json()
    assert json_v2["key_points"] == ["Point A", "Point B"]

    # Test v3 response with dicts -> must be normalized to list[str]
    resp_v3 = client.get(f"/api/v1/entry-analyses/{ea_v3_dict.id}")
    assert resp_v3.status_code == 200
    json_v3 = resp_v3.json()
    assert json_v3["key_points"] == ["Point from dict 1", "Point from dict 2"]
    assert all(isinstance(p, str) for p in json_v3["key_points"])


def test_api_grounding_evidence_and_raw_response_preserved(client: TestClient, db_session: Session):
    """Bloque 7G (D & E): raw_response preserves full structured output and grounding_evidence works."""
    src = Source(name="Test Source", type=SourceType.WEBSITE, provider="native", url="https://test.local/")
    db_session.add(src)
    db_session.flush()

    matrix = TrackingMatrix(code="TEST-MATRIX-2", name="Test Matrix 2", status="active")
    db_session.add(matrix)
    db_session.flush()

    prompt = AnalysisPromptVersion(
        code="observatory_deep_analysis",
        version=4,
        stage="deep_analysis",
        name="Deep v4",
        description="Deep v4",
        system_prompt="sys",
        user_prompt_template="user",
        response_schema_version="v3",
    )
    db_session.add(prompt)
    db_session.flush()

    entry = Entry(
        source_id=src.id,
        title="Test Entry 2",
        url="https://test.local/2",
        content="Sample content 2",
        content_hash="test_hash_2",
    )
    db_session.add(entry)
    db_session.flush()

    ea = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v4",
        status="completed",
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="hash_v4",
        key_points=["Extracted clean point"],
        summary="Summary v4",
    )
    db_session.add(ea)
    db_session.flush()

    raw_structured = {
        "result": {
            "summary": "Summary v4",
            "summary_evidence": [{"source_field": "content", "quote": "Sample content 2"}],
            "key_points": [
                {
                    "point": "Extracted clean point",
                    "evidence": [{"source_field": "content", "quote": "Sample content 2"}],
                }
            ],
        }
    }

    call = AnalysisCall(
        entry_analysis_id=ea.id,
        prompt_version_id=prompt.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        raw_response=raw_structured,
    )
    db_session.add(call)
    db_session.commit()

    resp = client.get(f"/api/v1/entry-analyses/{ea.id}")
    assert resp.status_code == 200
    data = resp.json()

    # key_points is strictly list[str]
    assert data["key_points"] == ["Extracted clean point"]

    # grounding_evidence is exposed separately
    assert data["grounding_evidence"] is not None
    assert len(data["grounding_evidence"]["summary_evidence"]) == 1
    assert data["grounding_evidence"]["summary_evidence"][0]["quote"] == "Sample content 2"
    assert len(data["grounding_evidence"]["key_points_evidence"]) == 1
    assert data["grounding_evidence"]["key_points_evidence"][0]["point"] == "Extracted clean point"
