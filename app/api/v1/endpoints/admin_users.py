"""Administrative endpoints for user and role management (Bloque 11A)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.core.security import hash_password, validate_password_policy
from app.db.session import get_db
from app.models.user import AuthSession, User, UserRole
from app.schemas.admin_user import (
    AdminResetPasswordRequest,
    AdminUserCreateRequest,
    AdminUserPublic,
    AdminUserUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/users", tags=["Admin Users"])


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency ensuring request is made by an active user with role='admin'."""
    if current_user.role != UserRole.ADMIN:
        logger.warning(
            "Non-admin user %s attempted to access admin endpoint",
            current_user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso restringido: se requieren permisos de administrador.",
        )
    return current_user


@router.get(
    "",
    response_model=list[AdminUserPublic],
    summary="List all users with roles and status",
)
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AdminUserPublic]:
    """Return complete list of registered users sorted by creation date."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [AdminUserPublic.model_validate(u) for u in users]


@router.post(
    "",
    response_model=AdminUserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user with specified role",
)
def create_user(
    payload: AdminUserCreateRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserPublic:
    """Create a new user with role 'admin' or 'user' and initial password."""
    # Check duplicate email
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe un usuario registrado con el correo electrónico '{payload.email}'.",
        )

    # Validate password policy
    valid, err_msg = validate_password_policy(payload.password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "La contraseña no cumple con los requisitos de seguridad mínimos.",
        )

    pw_hash = hash_password(payload.password)
    new_user = User(
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        password_hash=pw_hash,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    logger.info("Admin %s created user %s with role %s", _admin.email, new_user.email, new_user.role)
    return AdminUserPublic.model_validate(new_user)


@router.patch(
    "/{user_id}",
    response_model=AdminUserPublic,
    summary="Update user details, role, or active status",
)
def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdateRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserPublic:
    """Update user information with protection against removing the last active administrator."""
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado.",
        )

    # Protection: check if action would leave system without active admins
    is_target_active_admin = target_user.role == UserRole.ADMIN and target_user.is_active
    would_deactivate = payload.is_active is False
    would_demote = payload.role is not None and payload.role != UserRole.ADMIN

    if is_target_active_admin and (would_deactivate or would_demote):
        other_active_admins = (
            db.query(User)
            .filter(
                User.id != target_user.id,
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
            )
            .count()
        )
        if other_active_admins == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Operación denegada: no es posible desactivar o degradar al único administrador activo del sistema.",
            )

    # Check email duplicate if changed
    if payload.email and payload.email != target_user.email:
        existing = (
            db.query(User)
            .filter(User.email == payload.email, User.id != target_user.id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Ya existe otro usuario registrado con el correo '{payload.email}'.",
            )
        target_user.email = payload.email

    if payload.display_name is not None:
        target_user.display_name = payload.display_name

    if payload.role is not None:
        target_user.role = payload.role

    if payload.is_active is not None:
        target_user.is_active = payload.is_active
        # Revoke sessions immediately if user is deactivated
        if not payload.is_active:
            db.query(AuthSession).filter(AuthSession.user_id == target_user.id).delete()

    target_user.updated_at = utc_now()
    db.commit()
    db.refresh(target_user)

    logger.info("Admin %s updated user %s", _admin.email, target_user.email)
    return AdminUserPublic.model_validate(target_user)


@router.post(
    "/{user_id}/reset-password",
    summary="Reset user password administratively and revoke active sessions",
)
def reset_password(
    user_id: uuid.UUID,
    payload: AdminResetPasswordRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Reset password for target user and invalidate existing sessions."""
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado.",
        )

    valid, err_msg = validate_password_policy(payload.password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "La contraseña no cumple con los requisitos de seguridad mínimos.",
        )

    target_user.password_hash = hash_password(payload.password)
    target_user.updated_at = utc_now()

    # Revoke all active sessions so user must log in with new password
    revoked_count = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == target_user.id)
        .delete()
    )

    db.commit()

    logger.info(
        "Admin %s reset password for user %s (revoked %d sessions)",
        _admin.email,
        target_user.email,
        revoked_count,
    )
    return {
        "message": f"Contraseña actualizada correctamente para {target_user.email}. Se han revocado sus sesiones activas."
    }
