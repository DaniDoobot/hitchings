"""Comprehensive backend tests for administrative user and role management (Bloque 11A)."""

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
from app.models.user import AuthSession, User, UserRole

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


def _create_user_with_session(
    db_session: Session,
    client: TestClient,
    email: str,
    role: str = UserRole.USER,
    password: str = "CorrectPassword123!",
    is_active: bool = True,
) -> tuple[User, str]:
    """Helper creating user in DB and setting an active session cookie on client."""
    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        display_name=f"User {email.split('@')[0]}",
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
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


def test_unauthenticated_admin_endpoints_return_401(auth_client: TestClient):
    """All administrative endpoints return 401 when unauthenticated."""
    fake_id = uuid.uuid4()

    assert auth_client.get("/api/v1/admin/users").status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        auth_client.post(
            "/api/v1/admin/users",
            json={
                "email": "test@example.com",
                "display_name": "Test",
                "role": "user",
                "password": "Password123456!",
            },
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        auth_client.patch(
            f"/api/v1/admin/users/{fake_id}",
            json={"display_name": "Updated"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    assert (
        auth_client.post(
            f"/api/v1/admin/users/{fake_id}/reset-password",
            json={"password": "NewPassword1234!"},
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_normal_user_receives_403_forbidden(auth_client: TestClient, db_session: Session):
    """A regular user (role='user') receives 403 Forbidden on all admin endpoints."""
    user, token = _create_user_with_session(
        db_session, auth_client, "normal@example.com", role=UserRole.USER
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)
    fake_id = uuid.uuid4()

    res_list = auth_client.get("/api/v1/admin/users")
    assert res_list.status_code == status.HTTP_403_FORBIDDEN
    assert "restringido" in res_list.json()["detail"].lower()

    res_create = auth_client.post(
        "/api/v1/admin/users",
        json={
            "email": "another@example.com",
            "display_name": "Another",
            "role": "user",
            "password": "Password123456!",
        },
    )
    assert res_create.status_code == status.HTTP_403_FORBIDDEN

    res_patch = auth_client.patch(
        f"/api/v1/admin/users/{fake_id}",
        json={"display_name": "Hacker Edit"},
    )
    assert res_patch.status_code == status.HTTP_403_FORBIDDEN

    res_reset = auth_client.post(
        f"/api/v1/admin/users/{fake_id}/reset-password",
        json={"password": "NewPassword1234!"},
    )
    assert res_reset.status_code == status.HTTP_403_FORBIDDEN


def test_admin_can_list_users(auth_client: TestClient, db_session: Session):
    """Admin user can list all users with roles and active status."""
    admin, token = _create_user_with_session(
        db_session, auth_client, "admin@example.com", role=UserRole.ADMIN
    )
    user2, _ = _create_user_with_session(
        db_session, auth_client, "user2@example.com", role=UserRole.USER
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    res = auth_client.get("/api/v1/admin/users")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2

    emails = [u["email"] for u in data]
    assert "admin@example.com" in emails
    assert "user2@example.com" in emails

    admin_record = next(u for u in data if u["email"] == "admin@example.com")
    assert admin_record["role"] == UserRole.ADMIN
    assert admin_record["is_active"] is True


def test_admin_can_create_user_and_admin(auth_client: TestClient, db_session: Session):
    """Admin can create both regular users and new administrators."""
    admin, token = _create_user_with_session(
        db_session, auth_client, "admin_creator@example.com", role=UserRole.ADMIN
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # 1. Create regular user
    res_user = auth_client.post(
        "/api/v1/admin/users",
        json={
            "email": "new_client@example.com",
            "display_name": "Nuevo Cliente",
            "role": "user",
            "password": "SecurePassword123!",
        },
    )
    assert res_user.status_code == status.HTTP_201_CREATED
    user_data = res_user.json()
    assert user_data["email"] == "new_client@example.com"
    assert user_data["display_name"] == "Nuevo Cliente"
    assert user_data["role"] == "user"
    assert user_data["is_active"] is True

    # 2. Create another admin
    res_admin = auth_client.post(
        "/api/v1/admin/users",
        json={
            "email": "co_admin@example.com",
            "display_name": "Co-Administrador",
            "role": "admin",
            "password": "AdminPassword123!",
        },
    )
    assert res_admin.status_code == status.HTTP_201_CREATED
    admin_data = res_admin.json()
    assert admin_data["role"] == "admin"


def test_create_user_validation_and_conflict(auth_client: TestClient, db_session: Session):
    """Validates password length and rejects duplicate emails."""
    admin, token = _create_user_with_session(
        db_session, auth_client, "admin_val@example.com", role=UserRole.ADMIN
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # Short password (<12 chars) -> 400 or 422
    res_short = auth_client.post(
        "/api/v1/admin/users",
        json={
            "email": "short_pw@example.com",
            "display_name": "Short PW",
            "role": "user",
            "password": "short",
        },
    )
    assert res_short.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Duplicate email -> 409 Conflict
    res_dup = auth_client.post(
        "/api/v1/admin/users",
        json={
            "email": "admin_val@example.com",
            "display_name": "Duplicate",
            "role": "user",
            "password": "ValidPassword123!",
        },
    )
    assert res_dup.status_code == status.HTTP_409_CONFLICT
    assert "ya existe" in res_dup.json()["detail"].lower()


def test_admin_can_update_user_and_deactivate(auth_client: TestClient, db_session: Session):
    """Admin can edit user display name, email, promote role, and deactivate."""
    admin, token = _create_user_with_session(
        db_session, auth_client, "admin_editor@example.com", role=UserRole.ADMIN
    )
    target, target_token = _create_user_with_session(
        db_session, auth_client, "target_user@example.com", role=UserRole.USER
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # Update name and promote to admin
    res_patch = auth_client.patch(
        f"/api/v1/admin/users/{target.id}",
        json={
            "display_name": "Target Promoted",
            "role": "admin",
        },
    )
    assert res_patch.status_code == status.HTTP_200_OK
    assert res_patch.json()["display_name"] == "Target Promoted"
    assert res_patch.json()["role"] == "admin"

    # Deactivate target user
    res_deact = auth_client.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"is_active": False},
    )
    assert res_deact.status_code == status.HTTP_200_OK
    assert res_deact.json()["is_active"] is False

    # Verify target's active session was revoked in DB
    revoked_session = (
        db_session.query(AuthSession)
        .filter(AuthSession.user_id == target.id)
        .first()
    )
    assert revoked_session is None  # Deleted on deactivation


def test_protection_against_deactivating_or_demoting_last_active_admin(
    auth_client: TestClient, db_session: Session
):
    """System refuses to deactivate or demote the sole active administrator."""
    solo_admin, token = _create_user_with_session(
        db_session, auth_client, "solo_admin@example.com", role=UserRole.ADMIN
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, token)

    # 1. Attempt to deactivate self when only active admin
    res_deact = auth_client.patch(
        f"/api/v1/admin/users/{solo_admin.id}",
        json={"is_active": False},
    )
    assert res_deact.status_code == status.HTTP_400_BAD_REQUEST
    assert "único administrador" in res_deact.json()["detail"].lower()

    # 2. Attempt to demote self to user when only active admin
    res_demote = auth_client.patch(
        f"/api/v1/admin/users/{solo_admin.id}",
        json={"role": "user"},
    )
    assert res_demote.status_code == status.HTTP_400_BAD_REQUEST
    assert "único administrador" in res_demote.json()["detail"].lower()

    # 3. Add second admin: now demoting first admin succeeds
    admin2, _ = _create_user_with_session(
        db_session, auth_client, "admin_two@example.com", role=UserRole.ADMIN
    )
    res_demote_ok = auth_client.patch(
        f"/api/v1/admin/users/{solo_admin.id}",
        json={"role": "user"},
    )
    assert res_demote_ok.status_code == status.HTTP_200_OK
    assert res_demote_ok.json()["role"] == "user"


def test_admin_can_reset_password(auth_client: TestClient, db_session: Session):
    """Admin resets a user's password; existing session revoked; user can login with new password."""
    admin, admin_token = _create_user_with_session(
        db_session, auth_client, "admin_pwd@example.com", role=UserRole.ADMIN
    )
    user, user_token = _create_user_with_session(
        db_session,
        auth_client,
        "pwd_user@example.com",
        role=UserRole.USER,
        password="OldPassword123!",
    )
    auth_client.cookies.set(settings.AUTH_COOKIE_NAME, admin_token)

    # Reset password
    res_reset = auth_client.post(
        f"/api/v1/admin/users/{user.id}/reset-password",
        json={"password": "NewBrandPassword123!"},
    )
    assert res_reset.status_code == status.HTTP_200_OK
    assert "actualizada" in res_reset.json()["message"].lower()

    # User's old session is deleted
    old_session = (
        db_session.query(AuthSession)
        .filter(AuthSession.user_id == user.id)
        .first()
    )
    assert old_session is None

    # Verify user can log in with new password
    auth_client.cookies.clear()
    res_login = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "pwd_user@example.com", "password": "NewBrandPassword123!"},
    )
    assert res_login.status_code == status.HTTP_200_OK
    assert res_login.json()["user"]["role"] == "user"


def test_set_user_role_cli_script(db_session: Session):
    """Tests the scripts.set_user_role module with database session."""
    from scripts.set_user_role import set_user_role

    # Create a user to promote
    user = User(
        email="test_cli@doobot.ai",
        display_name="CLI Test User",
        password_hash=hash_password("DummyPassword123!"),
        role=UserRole.USER,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Promote to admin
    exit_code = set_user_role("test_cli@doobot.ai", "admin", db=db_session)
    assert exit_code == 0

    # Verify role in DB
    db_session.refresh(user)
    assert user.role == UserRole.ADMIN

    # Idempotency: setting admin again returns 0
    exit_code_idem = set_user_role("test_cli@doobot.ai", "admin", db=db_session)
    assert exit_code_idem == 0

    # Protection: sole active admin cannot be demoted
    exit_code_demote = set_user_role("test_cli@doobot.ai", "user", db=db_session)
    assert exit_code_demote == 1
    db_session.refresh(user)
    assert user.role == UserRole.ADMIN

    # Non-existent user returns 1
    assert set_user_role("nonexistent@doobot.ai", "admin", db=db_session) == 1

