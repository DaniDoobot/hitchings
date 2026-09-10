"""Comprehensive backend tests for editable tracking taxonomy (Bloque 11B)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import get_settings
from app.core.security import generate_session_token, hash_password, hash_session_token
from app.db.session import get_db
from app.main import app
from app.models.analysis import AnalysisCall, EntryAnalysis, EntryAnalysisTopic
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.models.user import AuthSession, User, UserRole
from app.services import observatory_query_service
from app.services.analysis_service import compute_analysis_input_hash

settings = get_settings()


@pytest.fixture
def auth_client(db_session: Session):
    """TestClient using the real get_current_user dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def ensure_active_matrix(db_session: Session) -> TrackingMatrix:
    """Ensure an active matrix with at least two Áreas and Temas exists for tests."""
    matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if matrix is None:
        matrix = TrackingMatrix(
            code=f"HITCHINGS-v0.{uuid.uuid4().hex[:4]}",
            name="Matriz de Seguimiento HITCHINGS",
            status="active",
        )
        db_session.add(matrix)
        db_session.flush()

    areas = db_session.query(TrackingTopic).filter(
        TrackingTopic.matrix_id == matrix.id,
        TrackingTopic.parent_id == None,
    ).all()
    if len(areas) < 2:
        area1 = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=None,
            code="derecho_competencia",
            name="Derecho de la Competencia",
            priority=1,
            active=True,
        )
        area2 = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=None,
            code="aplicacion_privada",
            name="Aplicación Privada",
            priority=2,
            active=True,
        )
        db_session.add_all([area1, area2])
        db_session.flush()
        areas = [area1, area2]

    for i, area in enumerate(areas):
        child = db_session.query(TrackingTopic).filter(
            TrackingTopic.matrix_id == matrix.id,
            TrackingTopic.parent_id == area.id,
        ).first()
        if child is None:
            child = TrackingTopic(
                matrix_id=matrix.id,
                parent_id=area.id,
                code=f"topic_{i}_{area.code}",
                name=f"Tema de {area.name}",
                priority=10 * (i + 1),
                active=True,
            )
            db_session.add(child)

    db_session.commit()
    return matrix


def _create_user_with_session(
    db_session: Session,
    email: str,
    role: str = UserRole.USER,
    password: str = "TaxonomyPass123!",
) -> tuple[User, str]:
    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        display_name=f"User {email.split('@')[0]}",
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(user)
    db_session.flush()

    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    session = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        last_seen_at=now,
    )
    db_session.add(session)
    db_session.commit()

    return user, raw_token


def test_taxonomy_unauthenticated_returns_401(auth_client: TestClient):
    """Taxonomy endpoints require authentication."""
    fake_id = uuid.uuid4()
    auth_client.cookies.clear()
    assert auth_client.get("/api/v1/taxonomy").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        auth_client.post(
            "/api/v1/taxonomy/areas",
            json={"base_matrix_id": str(fake_id), "name": "Test Area"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        auth_client.patch(
            f"/api/v1/taxonomy/areas/{fake_id}",
            json={"base_matrix_id": str(fake_id), "name": "Renamed Area"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_taxonomy_authenticated_read_and_permissions(
    db_session: Session, auth_client: TestClient
):
    """Both standard users and admins can read taxonomy."""
    user, token_user = _create_user_with_session(db_session, "standard@test.com", UserRole.USER)
    admin, token_admin = _create_user_with_session(db_session, "admin@test.com", UserRole.ADMIN)

    # Standard user
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token_user)
    res_user = auth_client.get("/api/v1/taxonomy")
    assert res_user.status_code == status.HTTP_200_OK
    data = res_user.json()
    assert "matrix_id" in data
    assert data["status"] == "active"
    assert len(data["areas"]) > 0

    # Admin user
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token_admin)
    res_admin = auth_client.get("/api/v1/taxonomy")
    assert res_admin.status_code == status.HTTP_200_OK
    assert res_admin.json()["matrix_id"] == data["matrix_id"]


def test_create_and_update_area_versions_matrix(
    db_session: Session, auth_client: TestClient
):
    """Creating and updating an area bumps matrix version, archiving previous one."""
    _, token = _create_user_with_session(db_session, "editor@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # 1. Read current active taxonomy
    get_res = auth_client.get("/api/v1/taxonomy")
    initial_tax = get_res.json()
    base_id = initial_tax["matrix_id"]
    initial_code = initial_tax["code"]

    # 2. Create new Area
    create_res = auth_client.post(
        "/api/v1/taxonomy/areas",
        json={
            "base_matrix_id": base_id,
            "name": "Ayudas de Estado",
            "description": "Control y litigación de ayudas estatales",
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    new_tax = create_res.json()

    assert new_tax["matrix_id"] != base_id
    assert new_tax["code"] != initial_code
    assert new_tax["status"] == "active"

    # Verify old matrix is archived
    old_m = db_session.query(TrackingMatrix).filter(TrackingMatrix.id == uuid.UUID(base_id)).first()
    assert old_m.status == "archived"

    # Verify new area in response
    new_area = next((a for a in new_tax["areas"] if a["name"] == "Ayudas de Estado"), None)
    assert new_area is not None
    assert new_area["code"] == "ayudas_de_estado"
    assert new_area["active"] is True
    assert new_area["description"] == "Control y litigación de ayudas estatales"

    # 3. Update the newly created area
    update_res = auth_client.patch(
        f"/api/v1/taxonomy/areas/{new_area['id']}",
        json={
            "base_matrix_id": new_tax["matrix_id"],
            "name": "Ayudas de Estado y Subvenciones",
            "description": "Descripción actualizada",
        },
    )
    assert update_res.status_code == status.HTTP_200_OK
    updated_tax = update_res.json()
    assert updated_tax["matrix_id"] != new_tax["matrix_id"]

    updated_area = next(
        (a for a in updated_tax["areas"] if a["name"] == "Ayudas de Estado y Subvenciones"), None
    )
    assert updated_area is not None
    assert updated_area["code"] == "ayudas_de_estado"
    assert updated_area["description"] == "Descripción actualizada"


def test_create_update_and_move_topic(
    db_session: Session, auth_client: TestClient
):
    """Creating a topic, editing it, and moving it to another area."""
    _, token = _create_user_with_session(db_session, "topic_editor@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    current_tax = auth_client.get("/api/v1/taxonomy").json()

    area_1 = current_tax["areas"][0]
    area_2 = current_tax["areas"][1]

    # Create Topic under area_1
    create_res = auth_client.post(
        "/api/v1/taxonomy/topics",
        json={
            "base_matrix_id": current_tax["matrix_id"],
            "area_id": area_1["id"],
            "name": "Subvenciones Extranjeras FSR",
            "description": "Reglamento sobre subvenciones extranjeras",
        },
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    tax_after_create = create_res.json()

    # Find the topic in area_1
    cloned_area_1 = next(a for a in tax_after_create["areas"] if a["code"] == area_1["code"])
    new_topic = next((t for t in cloned_area_1["children"] if t["code"] == "subvenciones_extranjeras_fsr"), None)
    assert new_topic is not None
    assert new_topic["name"] == "Subvenciones Extranjeras FSR"

    # Move topic to area_2 and rename it
    cloned_area_2 = next(a for a in tax_after_create["areas"] if a["code"] == area_2["code"])
    update_res = auth_client.patch(
        f"/api/v1/taxonomy/topics/{new_topic['id']}",
        json={
            "base_matrix_id": tax_after_create["matrix_id"],
            "area_id": cloned_area_2["id"],
            "name": "FSR y Subvenciones Extranjeras",
        },
    )
    assert update_res.status_code == status.HTTP_200_OK
    tax_after_move = update_res.json()

    # Verify topic is now in area_2 and renamed
    target_area = next(a for a in tax_after_move["areas"] if a["code"] == area_2["code"])
    moved_topic = next((t for t in target_area["children"] if t["code"] == "subvenciones_extranjeras_fsr"), None)
    assert moved_topic is not None
    assert moved_topic["name"] == "FSR y Subvenciones Extranjeras"


def test_archive_and_reactivate(
    db_session: Session, auth_client: TestClient
):
    """Archiving and reactivating topics and areas."""
    _, token = _create_user_with_session(db_session, "archiver@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    current_tax = auth_client.get("/api/v1/taxonomy").json()

    area = current_tax["areas"][0]
    topic = area["children"][0]

    # 1. Archive topic
    arch_res = auth_client.post(
        f"/api/v1/taxonomy/topics/{topic['id']}/archive",
        json={"base_matrix_id": current_tax["matrix_id"]},
    )
    assert arch_res.status_code == status.HTTP_200_OK
    tax_archived = arch_res.json()

    cloned_area = next(a for a in tax_archived["areas"] if a["code"] == area["code"])
    cloned_topic = next(t for t in cloned_area["children"] if t["code"] == topic["code"])
    assert cloned_topic["active"] is False

    # 2. Reactivate topic
    react_res = auth_client.post(
        f"/api/v1/taxonomy/topics/{cloned_topic['id']}/reactivate",
        json={"base_matrix_id": tax_archived["matrix_id"]},
    )
    assert react_res.status_code == status.HTTP_200_OK
    tax_reactivated = react_res.json()

    cloned_area_2 = next(a for a in tax_reactivated["areas"] if a["code"] == area["code"])
    cloned_topic_2 = next(t for t in cloned_area_2["children"] if t["code"] == topic["code"])
    assert cloned_topic_2["active"] is True

    # 3. Archive area (cascades to children)
    arch_area_res = auth_client.post(
        f"/api/v1/taxonomy/areas/{cloned_area_2['id']}/archive",
        json={"base_matrix_id": tax_reactivated["matrix_id"]},
    )
    assert arch_area_res.status_code == status.HTTP_200_OK
    tax_area_archived = arch_area_res.json()
    archived_area = next(a for a in tax_area_archived["areas"] if a["code"] == area["code"])
    assert archived_area["active"] is False
    assert all(child["active"] is False for child in archived_area["children"])

    # 4. Reactivate area
    react_area_res = auth_client.post(
        f"/api/v1/taxonomy/areas/{archived_area['id']}/reactivate",
        json={"base_matrix_id": tax_area_archived["matrix_id"]},
    )
    assert react_area_res.status_code == status.HTTP_200_OK
    tax_area_react = react_area_res.json()
    reactivated_area = next(a for a in tax_area_react["areas"] if a["code"] == area["code"])
    assert reactivated_area["active"] is True


def test_concurrency_conflict_returns_409(
    db_session: Session, auth_client: TestClient
):
    """Mutating with an outdated base_matrix_id returns 409 Conflict."""
    _, token = _create_user_with_session(db_session, "conflict@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    current_tax = auth_client.get("/api/v1/taxonomy").json()
    stale_matrix_id = current_tax["matrix_id"]

    # First mutation succeeds and bumps matrix version
    res_1 = auth_client.post(
        "/api/v1/taxonomy/areas",
        json={"base_matrix_id": stale_matrix_id, "name": "Concurrencia 1"},
    )
    assert res_1.status_code == status.HTTP_201_CREATED

    # Second concurrent mutation using the stale matrix ID must fail with 409
    res_2 = auth_client.post(
        "/api/v1/taxonomy/areas",
        json={"base_matrix_id": stale_matrix_id, "name": "Concurrencia 2"},
    )
    assert res_2.status_code == status.HTTP_409_CONFLICT
    assert "modificada por otro usuario" in res_2.json()["detail"]


def test_dashboard_continuity_by_code(
    db_session: Session, auth_client: TestClient
):
    """Renaming a topic preserves historical activity counts; newly added topic has 0 count."""
    _, token = _create_user_with_session(db_session, "analytics@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # 1. Create a dummy source, entry, and analysis with topic carteles_acuerdos
    now = datetime.now(timezone.utc)
    source = Source(
        id=uuid.uuid4(),
        name="Test Authority",
        type=SourceType.INSTITUTIONAL,
        url="https://authority.test",
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Cartel resolution 2026",
        url=f"https://authority.test/res/{uuid.uuid4().hex[:6]}",
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(entry)
    db_session.flush()

    active_matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    topic = db_session.query(TrackingTopic).filter(
        TrackingTopic.matrix_id == active_matrix.id,
        TrackingTopic.parent_id != None,
        TrackingTopic.active == True,
    ).first()
    assert topic is not None
    target_code = topic.code
    target_original_name = topic.name

    analysis = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        entry_content_hash=compute_analysis_input_hash(entry),
        matrix_snapshot={},
        status="completed",
        relevance_status="relevant",
        relevance_score=85,
        summary="Analysis on cartels",
        matrix_snapshot_hash="hash-abc",
        created_at=now,
        updated_at=now,
    )
    db_session.add(analysis)
    db_session.flush()

    analysis_topic = EntryAnalysisTopic(
        id=uuid.uuid4(),
        analysis_id=analysis.id,
        topic_id=topic.id,
        confidence=0.9,
        is_primary=True,
    )
    db_session.add(analysis_topic)
    db_session.commit()

    # Initial dashboard check
    dash_before = observatory_query_service.get_dashboard(db_session)
    top_t_before = next((t for t in dash_before.top_topics if t.code == target_code), None)
    assert top_t_before is not None
    assert top_t_before.name == target_original_name
    assert top_t_before.count >= 1

    # 2. Rename the topic in the taxonomy
    current_tax = auth_client.get("/api/v1/taxonomy").json()
    target_topic = None
    for a in current_tax["areas"]:
        for t in a["children"]:
            if t["code"] == target_code:
                target_topic = t
                break
        if target_topic:
            break
    assert target_topic is not None

    new_name = f"{target_original_name} (Nuevo Nombre)"
    update_res = auth_client.patch(
        f"/api/v1/taxonomy/topics/{target_topic['id']}",
        json={
            "base_matrix_id": current_tax["matrix_id"],
            "name": new_name,
        },
    )
    assert update_res.status_code == status.HTTP_200_OK

    # 3. Verify dashboard now displays new name with preserved count
    dash_after = observatory_query_service.get_dashboard(db_session)
    top_t_after = next((t for t in dash_after.top_topics if t.code == target_code), None)
    assert top_t_after is not None
    assert top_t_after.name == new_name
    assert top_t_after.count == top_t_before.count


def test_zero_reevaluations_and_db_counts_guarantee(
    db_session: Session, auth_client: TestClient
):
    """Taxonomy modifications strictly never invoke re-evaluations or Gemini."""
    initial_analysis_count = db_session.query(EntryAnalysis).count()
    initial_calls_count = db_session.query(AnalysisCall).count()

    _, token = _create_user_with_session(db_session, "guarantee@test.com", UserRole.USER)
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    tax = auth_client.get("/api/v1/taxonomy").json()

    # Perform multiple operations
    res_create = auth_client.post(
        "/api/v1/taxonomy/areas",
        json={"base_matrix_id": tax["matrix_id"], "name": "Tax Zero Reeval Area"},
    )
    assert res_create.status_code == status.HTTP_201_CREATED
    tax2 = res_create.json()

    area = next(a for a in tax2["areas"] if a["code"] == "tax_zero_reeval_area")
    res_top = auth_client.post(
        "/api/v1/taxonomy/topics",
        json={"base_matrix_id": tax2["matrix_id"], "area_id": area["id"], "name": "Subtopic Zero"},
    )
    assert res_top.status_code == status.HTTP_201_CREATED

    # Verify counts in DB remain strictly invariant
    assert db_session.query(EntryAnalysis).count() == initial_analysis_count
    assert db_session.query(AnalysisCall).count() == initial_calls_count
