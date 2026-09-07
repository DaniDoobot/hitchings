"""Tests for tracking and sources CRUD API endpoints."""

import uuid
from fastapi import status
from fastapi.testclient import TestClient


# ==============================================================================
# MATRIX API TESTS
# ==============================================================================

def test_matrix_lifecycle_and_activation(client: TestClient) -> None:
    """Test matrix creation, listing, partial update, and activation logic."""
    # 1. Create first matrix (draft)
    m1_resp = client.post(
        "/api/v1/tracking/matrices",
        json={
            "code": "MATRIX-01",
            "name": "Matriz Versión 1",
            "description": "Primera matriz",
            "status": "draft",
        }
    )
    assert m1_resp.status_code == status.HTTP_201_CREATED
    m1_data = m1_resp.json()
    m1_id = m1_data["id"]
    assert m1_data["status"] == "draft"

    # 2. Create second matrix (draft)
    m2_resp = client.post(
        "/api/v1/tracking/matrices",
        json={
            "code": "MATRIX-02",
            "name": "Matriz Versión 2",
            "status": "draft",
        }
    )
    assert m2_resp.status_code == status.HTTP_201_CREATED
    m2_id = m2_resp.json()["id"]

    # 3. Activate first matrix
    act1_resp = client.post(f"/api/v1/tracking/matrices/{m1_id}/activate")
    assert act1_resp.status_code == status.HTTP_200_OK
    assert act1_resp.json()["status"] == "active"
    assert act1_resp.json()["activated_at"] is not None

    # 4. Activate second matrix: first matrix should transition to 'archived'
    act2_resp = client.post(f"/api/v1/tracking/matrices/{m2_id}/activate")
    assert act2_resp.status_code == status.HTTP_200_OK
    assert act2_resp.json()["status"] == "active"

    # Verify first matrix is now archived
    m1_check = client.get(f"/api/v1/tracking/matrices/{m1_id}").json()
    assert m1_check["status"] == "archived"


# ==============================================================================
# TOPICS API TESTS
# ==============================================================================

def test_topics_crud_and_filters(client: TestClient) -> None:
    """Test topic creation, hierarchy, filtering, and deletion."""
    # Create matrix
    m_resp = client.post(
        "/api/v1/tracking/matrices",
        json={"code": "TOPIC-MATRIX", "name": "Matriz para Topics"}
    )
    matrix_id = m_resp.json()["id"]

    # Create parent topic
    p_resp = client.post(
        "/api/v1/tracking/topics",
        json={
            "matrix_id": matrix_id,
            "code": "competition_law",
            "name": "Derecho de la Competencia",
            "priority": 100,
            "provisional": False,
            "keywords": ["antitrust", "competition"],
        }
    )
    assert p_resp.status_code == status.HTTP_201_CREATED
    parent_id = p_resp.json()["id"]

    # Create subtopic
    s_resp = client.post(
        "/api/v1/tracking/topics",
        json={
            "matrix_id": matrix_id,
            "parent_id": parent_id,
            "code": "mergers",
            "name": "Control de Concentraciones",
            "priority": 50,
            "provisional": True,
        }
    )
    assert s_resp.status_code == status.HTTP_201_CREATED
    subtopic_id = s_resp.json()["id"]

    # Filter by parent_id
    filter_resp = client.get(f"/api/v1/tracking/topics?parent_id={parent_id}")
    assert filter_resp.status_code == status.HTTP_200_OK
    topics = filter_resp.json()
    assert len(topics) == 1
    assert topics[0]["code"] == "mergers"

    # Patch subtopic
    patch_resp = client.patch(
        f"/api/v1/tracking/topics/{subtopic_id}",
        json={"name": "Concentraciones y Fusiones"}
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["name"] == "Concentraciones y Fusiones"


# ==============================================================================
# ENTITIES & ASSOCIATIONS API TESTS
# ==============================================================================

def test_entities_and_topic_associations(client: TestClient) -> None:
    """Test tracked entities CRUD and linking to topics."""
    # 1. Create matrix & topic
    m_id = client.post("/api/v1/tracking/matrices", json={"code": "M-ASSOC", "name": "M Assoc"}).json()["id"]
    t_id = client.post(
        "/api/v1/tracking/topics",
        json={"matrix_id": m_id, "code": "private_enf", "name": "Aplicación Privada"}
    ).json()["id"]

    # 2. Create entity
    e_resp = client.post(
        "/api/v1/tracking/entities",
        json={
            "display_name": "Hausfeld",
            "entity_type": "organization",
            "metadata": {"section": "Aplicación privada"},
        }
    )
    assert e_resp.status_code == status.HTTP_201_CREATED
    entity_id = e_resp.json()["id"]
    assert e_resp.json()["display_name"] == "Hausfeld"

    # 3. Link entity to topic
    link_resp = client.post(
        f"/api/v1/tracking/entities/{entity_id}/topics/{t_id}",
        json={"is_primary": True, "notes": "Asignación principal"}
    )
    assert link_resp.status_code == status.HTTP_201_CREATED
    assert link_resp.json()["is_primary"] is True

    # 4. List entity topics
    list_resp = client.get(f"/api/v1/tracking/entities/{entity_id}/topics")
    assert list_resp.status_code == status.HTTP_200_OK
    entity_topics = list_resp.json()
    assert len(entity_topics) == 1
    assert entity_topics[0]["code"] == "private_enf"

    # 5. Unlink entity from topic
    unlink_resp = client.delete(f"/api/v1/tracking/entities/{entity_id}/topics/{t_id}")
    assert unlink_resp.status_code == status.HTTP_204_NO_CONTENT

    # Verify list is now empty
    check_resp = client.get(f"/api/v1/tracking/entities/{entity_id}/topics")
    assert len(check_resp.json()) == 0


# ==============================================================================
# SOURCES CRUD API TESTS
# ==============================================================================

def test_sources_crud_api(client: TestClient) -> None:
    """Test full sources CRUD endpoints including tracked_entity_id linkage."""
    # 1. Create tracked entity
    entity = client.post(
        "/api/v1/tracking/entities",
        json={"display_name": "Pinar Akman", "entity_type": "person"}
    ).json()
    entity_id = entity["id"]

    # 2. Create source linked to entity
    src_resp = client.post(
        "/api/v1/sources",
        json={
            "name": "Pinar Akman Blog / Web",
            "type": "website",
            "url": "https://pinarakman.example.com",
            "provider": "native",
            "tracked_entity_id": entity_id,
            "category": "Academia",
        }
    )
    assert src_resp.status_code == status.HTTP_201_CREATED
    source_id = src_resp.json()["id"]
    assert src_resp.json()["tracked_entity_id"] == entity_id

    # 3. Retrieve source
    get_resp = client.get(f"/api/v1/sources/{source_id}")
    assert get_resp.status_code == status.HTTP_200_OK
    assert get_resp.json()["name"] == "Pinar Akman Blog / Web"

    # 4. Filter sources by tracked_entity_id
    filter_resp = client.get(f"/api/v1/sources?tracked_entity_id={entity_id}")
    assert filter_resp.status_code == status.HTTP_200_OK
    assert len(filter_resp.json()) == 1

    # 5. Patch source
    patch_resp = client.patch(
        f"/api/v1/sources/{source_id}",
        json={"active": False, "category": "Investigación"}
    )
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.json()["active"] is False
    assert patch_resp.json()["category"] == "Investigación"

    # 6. Delete source
    del_resp = client.delete(f"/api/v1/sources/{source_id}")
    assert del_resp.status_code == status.HTTP_204_NO_CONTENT

    # Verify 404 on deleted source
    assert client.get(f"/api/v1/sources/{source_id}").status_code == status.HTTP_404_NOT_FOUND
