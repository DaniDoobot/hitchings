"""Authentication endpoints and dependencies for the private client portal (Bloque 8C.1)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.security import (
    dummy_verify_password,
    generate_session_token,
    hash_session_token,
    verify_password,
)
from app.db.session import get_db
from app.models.user import AuthSession, User
from app.schemas.auth import LoginRequest, LoginResponse, UserPublic

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["Authentication"])


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


# In-memory sliding window rate limiter for login attempts (MVP single-process protection)
# Map: key (IP or email) -> list of failed attempt timestamps (time.time())
_FAILED_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW_SECONDS = 60
_MAX_FAILED_ATTEMPTS = 5


def _check_rate_limit(key: str) -> None:
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    # Purge old attempts
    attempts = [t for t in _FAILED_ATTEMPTS[key] if t > cutoff]
    _FAILED_ATTEMPTS[key] = attempts
    if len(attempts) >= _MAX_FAILED_ATTEMPTS:
        logger.warning("Rate limit exceeded for authentication key: %s", key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos de inicio de sesión. Por favor, espere un minuto antes de reintentar.",
        )


def _record_failed_attempt(key: str) -> None:
    now = time.time()
    _FAILED_ATTEMPTS[key].append(now)


def _clear_failed_attempts(key: str) -> None:
    _FAILED_ATTEMPTS.pop(key, None)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Authenticate request using HttpOnly session cookie and server-side session hash.
    
    Raises HTTPException(401) if cookie is missing, token is invalid/revoked/expired,
    or user account is inactive.
    """
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado.",
        )

    token_hash = hash_session_token(token)
    session = (
        db.query(AuthSession)
        .options(joinedload(AuthSession.user))
        .filter(AuthSession.token_hash == token_hash)
        .first()
    )

    if not session or session.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no válida o revocada.",
        )

    now = utc_now()
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión expirada.",
        )

    if not session.user or not session.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo.",
        )

    # Touch session last_seen_at
    session.last_seen_at = now
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("Failed to update session last_seen_at: %s", exc)

    return session.user


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate user and establish server-side HttpOnly session cookie",
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """Validate user credentials, establish server-side session, and issue HttpOnly cookie."""
    client_ip = request.client.host if request.client else "unknown"
    normalized_email = payload.email.strip().lower()
    rate_key = f"{client_ip}:{normalized_email}"

    _check_rate_limit(rate_key)

    user = db.query(User).filter(User.email == normalized_email).first()

    if not user:
        dummy_verify_password(payload.password)
        _record_failed_attempt(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
        )

    if not verify_password(payload.password, user.password_hash) or not user.is_active:
        _record_failed_attempt(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
        )

    # Authentication succeeded: reset rate limiter for this key
    _clear_failed_attempts(rate_key)

    now = utc_now()
    expires_at = now + timedelta(hours=settings.AUTH_SESSION_TTL_HOURS)
    raw_token = generate_session_token()
    token_hash = hash_session_token(raw_token)

    auth_session = AuthSession(
        user_id=user.id,
        token_hash=token_hash,
        created_at=now,
        expires_at=expires_at,
        last_seen_at=now,
    )
    user.last_login_at = now

    db.add(auth_session)
    db.commit()
    db.refresh(user)

    # Set secure HttpOnly cookie
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=raw_token,
        max_age=settings.AUTH_SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.AUTH_COOKIE_SECURE,
        domain=settings.AUTH_COOKIE_DOMAIN or None,
        path="/",
    )

    return LoginResponse(user=UserPublic.model_validate(user))


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get currently authenticated user profile",
)
def get_me(
    current_user: User = Depends(get_current_user),
) -> UserPublic:
    """Return public details of currently authenticated user."""
    return UserPublic.model_validate(current_user)


@router.post(
    "/logout",
    summary="Revoke current session and remove HttpOnly cookie",
)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Revoke session in database and delete browser session cookie."""
    token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if token:
        token_hash = hash_session_token(token)
        session = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
            .first()
        )
        if session:
            session.revoked_at = utc_now()
            db.commit()

    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        path="/",
        domain=settings.AUTH_COOKIE_DOMAIN or None,
    )

    return {"message": "Sesión cerrada correctamente"}
