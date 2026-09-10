"""Administrative CLI script to set or change portal user authorization roles (Bloque 11A).

Usage:
    python -m scripts.set_user_role --email dani@doobot.ai --role admin
    python -m scripts.set_user_role --email usuario@example.com --role user
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.user import User, UserRole


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def set_user_role(
    email: str,
    role: str,
    db: Session | None = None,
) -> int:
    """Set authorization role for given email.
    
    Returns 0 on success, 1 on failure.
    """
    normalized_email = email.strip().lower()
    target_role = role.strip().lower()

    if not normalized_email or "@" not in normalized_email:
        print(f"Error: La dirección de correo '{email}' no es válida.", file=sys.stderr)
        return 1

    if target_role not in (UserRole.ADMIN, UserRole.USER):
        print(f"Error: Rol '{role}' no válido. Use 'admin' o 'user'.", file=sys.stderr)
        return 1

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            print(
                f"Error: No se encontró ningún usuario registrado con el email '{normalized_email}'.",
                file=sys.stderr,
            )
            return 1

        if user.role == target_role:
            print(
                f"El usuario '{normalized_email}' ya tiene asignado el rol '{target_role}'. No se requieren cambios."
            )
            return 0

        # Guardrail: prevent demoting the only active administrator
        if user.role == UserRole.ADMIN and target_role != UserRole.ADMIN and user.is_active:
            other_admins = (
                db.query(User)
                .filter(
                    User.id != user.id,
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
                .count()
            )
            if other_admins == 0:
                print(
                    "Error de seguridad: No es posible degradar al único administrador activo del sistema.",
                    file=sys.stderr,
                )
                return 1

        previous_role = user.role
        user.role = target_role
        user.updated_at = utc_now()

        db.commit()
        db.refresh(user)

        print("Rol de usuario actualizado con éxito:")
        print(f"  ID:           {user.id}")
        print(f"  Email:        {user.email}")
        print(f"  Display Name: {user.display_name}")
        print(f"  Rol Anterior: {previous_role}")
        print(f"  Nuevo Rol:    {user.role}")
        print(f"  Activo:       {user.is_active}")
        return 0

    except Exception as exc:
        db.rollback()
        print(f"Error inesperado al actualizar el rol: {exc}", file=sys.stderr)
        return 1
    finally:
        if close_db:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gestión administrativa de roles para usuarios del portal HITCHINGS Y GONZALEZ."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Dirección de correo electrónico del usuario (se normalizará a minúsculas).",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=[UserRole.ADMIN, UserRole.USER],
        help=f"Nuevo rol para el usuario: '{UserRole.ADMIN}' o '{UserRole.USER}'.",
    )

    args = parser.parse_args(argv)
    return set_user_role(args.email, args.role)


if __name__ == "__main__":
    sys.exit(main())
