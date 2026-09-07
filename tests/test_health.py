"""Tests for health check endpoints."""

from unittest.mock import MagicMock
from fastapi import status
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import get_db


def test_health_endpoint(client: TestClient) -> None:
    """Test that GET /health returns 200 and expected payload."""
    response = client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "hitchings"


def test_health_db_endpoint_success(client: TestClient) -> None:
    """Test GET /health/db when database connection succeeds."""
    mock_db = MagicMock()
    mock_db.execute.return_value = None

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/health/db")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ok"
        assert data["database"] == "connected"
    finally:
        app.dependency_overrides.clear()


def test_health_db_endpoint_failure(client: TestClient) -> None:
    """Test GET /health/db when database connection fails, ensuring /health still works."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("Connection refused")

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        # /health/db should report failure
        response = client.get("/health/db")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["status"] == "error"
        assert data["database"] == "disconnected"

        # Main /health must remain 200 OK even when DB fails
        base_health = client.get("/health")
        assert base_health.status_code == status.HTTP_200_OK
        assert base_health.json()["status"] == "ok"
    finally:
        app.dependency_overrides.clear()
