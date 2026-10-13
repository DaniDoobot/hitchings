"""Tests for Bloque 11C Phase 1: Infrastructure and Canonical Manual Source."""

import importlib
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from app.models.source import (
    Source,
    SourceType,
    MANUAL_UPLOAD_SOURCE_ID,
    MANUAL_UPLOAD_SOURCE_NAME,
    MANUAL_UPLOAD_SOURCE_CODE,
    MANUAL_UPLOAD_SOURCE_URL,
    get_or_create_manual_source,
)
from scripts.seed_source_manual_upload import seed_manual_upload_source


def test_requirements_declares_python_multipart():
    """Verify that python-multipart is explicitly declared in requirements.txt."""
    req_file = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert req_file.exists(), "requirements.txt not found"
    content = req_file.read_text(encoding="utf-8")
    assert "python-multipart" in content, "python-multipart missing from requirements.txt"


def test_python_multipart_is_importable():
    """Verify that python-multipart package is installed and importable in environment."""
    multipart_mod = importlib.import_module("multipart")
    assert hasattr(multipart_mod, "__version__") or hasattr(multipart_mod, "parse_options_header")


def test_nginx_config_contains_client_max_body_size():
    """Verify that nginx.conf configures client_max_body_size 25M for file uploads."""
    nginx_file = Path(__file__).resolve().parents[1] / "nginx.conf"
    assert nginx_file.exists(), "nginx.conf not found"
    content = nginx_file.read_text(encoding="utf-8")
    assert "client_max_body_size 25M;" in content, "client_max_body_size 25M missing from nginx.conf"


def test_manual_source_constants():
    """Verify canonical source constants are defined with correct types and values."""
    assert str(MANUAL_UPLOAD_SOURCE_ID) == "a0000000-0000-4000-a000-000000000001"
    assert MANUAL_UPLOAD_SOURCE_NAME == "Aportación Manual / Fondo Documental"
    assert MANUAL_UPLOAD_SOURCE_CODE == "manual_upload"
    assert MANUAL_UPLOAD_SOURCE_URL == "https://hitchings.internal/manual-upload"


def test_get_or_create_manual_source_creates_with_correct_values(db_session: Session):
    """Verify get_or_create_manual_source creates the canonical source with valid values."""
    # Ensure starting clean in test session
    existing = db_session.query(Source).filter(Source.id == MANUAL_UPLOAD_SOURCE_ID).first()
    if existing:
        db_session.delete(existing)
        db_session.commit()

    source = get_or_create_manual_source(db_session)
    db_session.commit()

    assert source is not None
    assert source.id == MANUAL_UPLOAD_SOURCE_ID
    assert source.name == MANUAL_UPLOAD_SOURCE_NAME
    assert source.type == SourceType.WEBSITE
    assert isinstance(source.type, SourceType)
    assert source.active is True
    assert source.category == "manual_document"
    assert source.provider == "native"
    assert source.url == MANUAL_UPLOAD_SOURCE_URL
    assert isinstance(source.config, dict)
    assert source.config.get("code") == MANUAL_UPLOAD_SOURCE_CODE
    assert source.config.get("ingestion_mode") == "manual"


def test_get_or_create_manual_source_is_strictly_idempotent(db_session: Session):
    """Verify executing creation mechanism multiple times does not duplicate source."""
    source1 = get_or_create_manual_source(db_session)
    db_session.commit()

    source2 = get_or_create_manual_source(db_session)
    db_session.commit()

    source3 = get_or_create_manual_source(db_session)
    db_session.commit()

    assert source1.id == source2.id == source3.id
    
    # Query database to confirm strictly 1 row matches
    matching = db_session.query(Source).filter(
        (Source.id == MANUAL_UPLOAD_SOURCE_ID) | (Source.name == MANUAL_UPLOAD_SOURCE_NAME)
    ).all()
    assert len(matching) == 1


def test_seed_script_executes_idempotently(db_session: Session):
    """Verify seed_manual_upload_source script function returns 0 and is idempotent."""
    res1 = seed_manual_upload_source()
    assert res1 == 0

    res2 = seed_manual_upload_source()
    assert res2 == 0
