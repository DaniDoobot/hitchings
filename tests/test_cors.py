"""Tests for CORS middleware and origins configuration (BLOQUE 8B.0)."""

from fastapi import status
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings


def test_cors_allowed_origin_receives_cors_headers(client: TestClient) -> None:
    """Verify an allowed origin receives Access-Control-Allow-Origin and Credentials."""
    settings = get_settings()
    original_origins = list(settings.CORS_ALLOWED_ORIGINS)
    try:
        settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173", "http://localhost:3000"]
        resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert resp.headers.get("access-control-allow-credentials") == "true"
        assert resp.headers.get("vary") == "Origin"
    finally:
        settings.CORS_ALLOWED_ORIGINS = original_origins


def test_cors_disallowed_origin_does_not_receive_authorization(client: TestClient) -> None:
    """Verify an origin not in CORS_ALLOWED_ORIGINS receives no CORS headers."""
    settings = get_settings()
    original_origins = list(settings.CORS_ALLOWED_ORIGINS)
    try:
        settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
        resp = client.get("/health", headers={"Origin": "http://malicious-site.com"})
        assert resp.status_code == status.HTTP_200_OK
        assert "access-control-allow-origin" not in resp.headers
    finally:
        settings.CORS_ALLOWED_ORIGINS = original_origins


def test_cors_preflight_options_allowed_origin(client: TestClient) -> None:
    """Verify preflight OPTIONS request returns 200 with CORS headers for allowed origin."""
    settings = get_settings()
    original_origins = list(settings.CORS_ALLOWED_ORIGINS)
    try:
        settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
        resp = client.options(
            "/api/v1/observatory/entries",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "GET" in resp.headers.get("access-control-allow-methods", "")
        assert resp.headers.get("access-control-allow-credentials") == "true"
    finally:
        settings.CORS_ALLOWED_ORIGINS = original_origins


def test_cors_preflight_options_disallowed_origin(client: TestClient) -> None:
    """Verify preflight OPTIONS request fails with 400 for unauthorized origin."""
    settings = get_settings()
    original_origins = list(settings.CORS_ALLOWED_ORIGINS)
    try:
        settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
        resp = client.options(
            "/api/v1/observatory/entries",
            headers={
                "Origin": "http://unauthorized-origin.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "access-control-allow-origin" not in resp.headers
    finally:
        settings.CORS_ALLOWED_ORIGINS = original_origins


def test_cors_empty_config_does_not_open_wildcard(client: TestClient) -> None:
    """Verify that when CORS_ALLOWED_ORIGINS is empty, no origin is granted access."""
    settings = get_settings()
    original_origins = list(settings.CORS_ALLOWED_ORIGINS)
    try:
        settings.CORS_ALLOWED_ORIGINS = []
        # Request with any origin
        resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.status_code == status.HTTP_200_OK
        assert "access-control-allow-origin" not in resp.headers

        # Preflight options also rejected
        opt_resp = client.options(
            "/api/v1/observatory/dashboard",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert opt_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "access-control-allow-origin" not in opt_resp.headers
    finally:
        settings.CORS_ALLOWED_ORIGINS = original_origins


def test_existing_api_works_without_cors_headers_on_internal_calls(client: TestClient) -> None:
    """Verify regular non-browser API calls (without Origin header) execute normally."""
    resp = client.get("/health")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == "ok"
    assert "access-control-allow-origin" not in resp.headers