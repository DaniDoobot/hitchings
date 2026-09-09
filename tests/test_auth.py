"""Comprehensive backend tests for private portal authentication (Bloque 8C.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.config import get_settings
from app.core.security import (
    dummy_verify_password,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.db.session import get_db
from app.main import app
from app.models.user import AuthSession, User

settings = get_settings()


@pytest.fixture
def real_auth_client(db_session: Session):
    """TestClient that uses the real get_current_user dependency (not overridden)."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # Ensure real get_current_user is tested
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


def test_password_hashing_and_verification():
    """Argon2id produces non-reversible hashes and verifies matching passwords."""
    raw = "MiPasswordSeguro2026!"
    pw_hash = hash_password(raw)
    assert pw_hash.startswith("$argon2id$")
    assert verify_password(raw, pw_hash) is True
    assert verify_password("wrong_password", pw_hash) is False
    assert verify_password("", pw_hash) is False


def test_session_token_hashing():
    """Session token is 32 bytes urlsafe and its hash is 64 hex chars (sha256)."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    assert len(raw_token) >= 40
    assert len(token_hash) == 64
    # Hash is deterministic
    assert hash_session_token(raw_token) == token_hash


def test_login_success_and_cookie_establishment(real_auth_client: TestClient, db_session: Session):
    """Successful login establishes HttpOnly cookie and persists session with hashed token."""
    raw_password = "password123!"
    user = User(
        email="cliente@example.com",
        display_name="Cliente Principal",
        password_hash=hash_password(raw_password),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = real_auth_client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@example.com", "password": raw_password},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "user" in data
    assert data["user"]["email"] == "cliente@example.com"
    assert data["user"]["display_name"] == "Cliente Principal"
    assert "password_hash" not in data["user"]

    # Cookie assertions
    cookie = response.cookies.get(settings.AUTH_COOKIE_NAME)
    assert cookie is not None

    # Check database persistence: session exists with token hash, not plaintext
    token_hash = hash_session_token(cookie)
    db_session.expire_all()
    saved_session = db_session.query(AuthSession).filter(AuthSession.token_hash == token_hash).first()
    assert saved_session is not None
    assert saved_session.user_id == user.id
    assert saved_session.revoked_at is None
    exp = saved_session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc)


def test_login_invalid_password_returns_generic_401(real_auth_client: TestClient, db_session: Session):
    """Invalid password returns generic 401 without exposing internals."""
    user = User(
        email="valid@example.com",
        display_name="Valid User",
        password_hash=hash_password("correct_pass"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = real_auth_client.post(
        "/api/v1/auth/login",
        json={"email": "valid@example.com", "password": "wrong_pass"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Credenciales incorrectas."


def test_login_non_existent_email_returns_identical_401(real_auth_client: TestClient):
    """Non-existent email returns same generic 401 to prevent user enumeration."""
    response = real_auth_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "any_password"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Credenciales incorrectas."


def test_login_inactive_user_rejected(real_auth_client: TestClient, db_session: Session):
    """Inactive user is rejected even with valid credentials."""
    user = User(
        email="inactive@example.com",
        display_name="Inactive User",
        password_hash=hash_password("password123"),
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    response = real_auth_client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "password123"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Credenciales incorrectas."


def test_me_authenticated_returns_profile(real_auth_client: TestClient, db_session: Session):
    """GET /auth/me returns public user details when session cookie is provided."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    user = User(
        email="me_user@example.com",
        display_name="Profile User",
        password_hash="hash",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    sess = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(sess)
    db_session.commit()

    real_auth_client.cookies.set(settings.AUTH_COOKIE_NAME, raw_token)
    response = real_auth_client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "id": str(user.id),
        "email": "me_user@example.com",
        "display_name": "Profile User",
    }


def test_me_unauthenticated_returns_401(real_auth_client: TestClient):
    """GET /auth/me without cookie returns 401."""
    response = real_auth_client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "No autenticado."


def test_me_expired_session_returns_401(real_auth_client: TestClient, db_session: Session):
    """Expired session cookie returns 401."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    user = User(
        email="expired@example.com",
        display_name="Expired User",
        password_hash="hash",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    sess = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=datetime.now(timezone.utc) - timedelta(hours=48),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc) - timedelta(hours=24),
    )
    db_session.add(sess)
    db_session.commit()

    real_auth_client.cookies.set(settings.AUTH_COOKIE_NAME, raw_token)
    response = real_auth_client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Sesión expirada."


def test_me_revoked_session_returns_401(real_auth_client: TestClient, db_session: Session):
    """Revoked session cookie returns 401."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    user = User(
        email="revoked@example.com",
        display_name="Revoked User",
        password_hash="hash",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    sess = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        last_seen_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc),
    )
    db_session.add(sess)
    db_session.commit()

    real_auth_client.cookies.set(settings.AUTH_COOKIE_NAME, raw_token)
    response = real_auth_client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Sesión no válida o revocada."


def test_logout_revokes_session_and_clears_cookie(real_auth_client: TestClient, db_session: Session):
    """POST /auth/logout revokes session in DB, deletes cookie, and blocks subsequent /me calls."""
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)
    user = User(
        email="logout@example.com",
        display_name="Logout User",
        password_hash="hash",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    sess = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(sess)
    db_session.commit()

    real_auth_client.cookies.set(settings.AUTH_COOKIE_NAME, raw_token)
    logout_res = real_auth_client.post("/api/v1/auth/logout")
    assert logout_res.status_code == status.HTTP_200_OK

    # Session in DB marked as revoked
    db_session.expire_all()
    sess_in_db = db_session.query(AuthSession).filter(AuthSession.token_hash == token_hash).first()
    assert sess_in_db is not None
    assert sess_in_db.revoked_at is not None

    # Subsequent /me without cookie returns 401
    me_res = real_auth_client.get("/api/v1/auth/me")
    assert me_res.status_code == status.HTTP_401_UNAUTHORIZED


def test_observatory_endpoints_require_authentication(real_auth_client: TestClient):
    """Observatory endpoints return 401 when accessed without active session cookie."""
    res_dash = real_auth_client.get("/api/v1/observatory/dashboard")
    assert res_dash.status_code == status.HTTP_401_UNAUTHORIZED

    res_entries = real_auth_client.get("/api/v1/observatory/entries")
    assert res_entries.status_code == status.HTTP_401_UNAUTHORIZED

    res_sources = real_auth_client.get("/api/v1/observatory/sources")
    assert res_sources.status_code == status.HTTP_401_UNAUTHORIZED

    res_topics = real_auth_client.get("/api/v1/observatory/topics")
    assert res_topics.status_code == status.HTTP_401_UNAUTHORIZED


def test_health_endpoints_remain_public(real_auth_client: TestClient):
    """Infrastructure health endpoints remain public without requiring authentication."""
    res_health = real_auth_client.get("/health")
    assert res_health.status_code == status.HTTP_200_OK

    res_health_db = real_auth_client.get("/health/db")
    assert res_health_db.status_code == status.HTTP_200_OK


def test_reset_user_password_updates_hash_and_revokes_sessions(db_session: Session):
    """reset_user_password updates user hash, updated_at, and revokes all active sessions."""
    from scripts.reset_user_password import reset_user_password

    # Setup user with 2 active sessions and 1 already revoked
    old_pw = "OldPassword123!"
    user = User(
        email="reset_target@example.com",
        display_name="Reset Target",
        password_hash=hash_password(old_pw),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    s1 = AuthSession(
        user_id=user.id,
        token_hash="hash_s1_unique_test",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc),
    )
    s2 = AuthSession(
        user_id=user.id,
        token_hash="hash_s2_unique_test",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc),
    )
    already_revoked = AuthSession(
        user_id=user.id,
        token_hash="hash_s3_already_revoked",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        last_seen_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add_all([s1, s2, already_revoked])
    db_session.commit()

    old_updated_at = user.updated_at
    new_pw = "NewSecurePassword456!"

    # Execute reset with password argument (programmatic / test mode)
    ret = reset_user_password("reset_target@example.com", password=new_pw, db=db_session)
    assert ret == 0

    # Refresh db state
    db_session.expire_all()
    reloaded_user = db_session.query(User).filter(User.id == user.id).first()
    assert reloaded_user is not None
    assert verify_password(new_pw, reloaded_user.password_hash) is True
    assert verify_password(old_pw, reloaded_user.password_hash) is False
    assert reloaded_user.updated_at >= old_updated_at

    # Verify all sessions are now revoked
    user_sessions = db_session.query(AuthSession).filter(AuthSession.user_id == user.id).all()
    assert len(user_sessions) == 3
    for s in user_sessions:
        assert s.revoked_at is not None
        assert s.is_valid() is False


def test_password_policy_validation():
    """validate_password_policy enforces MIN_PASSWORD_LENGTH (12) without requiring arbitrary symbols."""
    from app.core.security import MIN_PASSWORD_LENGTH, validate_password_policy

    assert MIN_PASSWORD_LENGTH == 12

    # Empty
    valid, msg = validate_password_policy("")
    assert valid is False
    assert "vacía" in msg.lower()

    # Short (11 chars)
    valid, msg = validate_password_policy("abcdefghijk")
    assert valid is False
    assert "12 caracteres" in msg

    # Exact 12 chars
    valid, msg = validate_password_policy("abcdefghijkl")
    assert valid is True
    assert msg is None

    # Longer
    valid, msg = validate_password_policy("unafraseseguradecompetencia")
    assert valid is True
    assert msg is None


def test_reset_user_password_validations(db_session: Session):
    """reset_user_password handles non-existent user, short password, and invalid email."""
    from scripts.reset_user_password import reset_user_password

    # Non-existent email
    assert reset_user_password("nonexistent@example.com", password="ValidPassword123!", db=db_session) == 1

    # Invalid email format
    assert reset_user_password("invalid_email", password="ValidPassword123!", db=db_session) == 1

    # User exists but password is too short (< 12 chars)
    user = User(
        email="short_pw_test@example.com",
        display_name="Short PW User",
        password_hash=hash_password("ValidInitialPassword123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    assert reset_user_password("short_pw_test@example.com", password="shortpass11", db=db_session) == 1
    assert reset_user_password("short_pw_test@example.com", password="123", db=db_session) == 1
