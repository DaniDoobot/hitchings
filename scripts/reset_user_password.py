"""Administrative CLI script to reset a user's password and revoke active sessions (Bloque 8C.1A).

Usage:
    python -m scripts.reset_user_password --email usuario@example.com
    # Interactively prompts for hidden new password and confirmation via getpass.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import sys

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import AuthSession, User


def _read_password_securely(prompt: str) -> str:
    """Prompt for a password without echoing.
    
    Uses getpass.getpass in interactive TTY environments, falling back
    cleanly to sys.stdin.readline when stdin is redirected/piped (e.g. CI/CD or automation).
    """
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        return ""
    return line.rstrip("\r\n")


def reset_user_password(
    email: str,
    password: str | None = None,
    db: Session | None = None,
) -> int:
    """Reset password for given email and revoke all active sessions.
    
    If password is None, prompts interactively using getpass.
    """
    normalized_email = email.strip().lower()
    if not normalized_email or "@" not in normalized_email:
        print(f"Error: La dirección de correo '{email}' no es válida.", file=sys.stderr)
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

        if password is None:
            p1 = _read_password_securely("Nueva contraseña: ")
            if not p1:
                print("Error: La contraseña no puede estar vacía.", file=sys.stderr)
                return 1
            if len(p1) < 6:
                print("Error: La contraseña debe tener al menos 6 caracteres.", file=sys.stderr)
                return 1
            p2 = _read_password_securely("Confirmar nueva contraseña: ")
            if p1 != p2:
                print("Error: Las contraseñas introducidas no coinciden.", file=sys.stderr)
                return 1
            password = p1
        else:
            if len(password) < 6:
                print("Error: La contraseña debe tener al menos 6 caracteres.", file=sys.stderr)
                return 1

        now = datetime.now(timezone.utc)
        pw_hash = hash_password(password)
        user.password_hash = pw_hash
        user.updated_at = now

        # Revoke all active sessions for this user
        revoked_count = (
            db.query(AuthSession)
            .filter(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
            .update({AuthSession.revoked_at: now}, synchronize_session=False)
        )

        db.commit()

        print(f"Contraseña actualizada con éxito para el usuario: {user.email}")
        print(f"  ID de usuario:        {user.id}")
        print(f"  Sesiones revocadas:   {revoked_count}")
        print(f"  Fecha de actualización: {now.isoformat()}")
        return 0

    except Exception as exc:
        db.rollback()
        print(f"Error inesperado al restablecer la contraseña: {exc}", file=sys.stderr)
        return 1
    finally:
        if close_db:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restablecimiento administrativo de contraseña y revocación de sesiones para usuarios de HITCHINGS."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Dirección de correo electrónico del usuario cuya contraseña se desea restablecer.",
    )

    args = parser.parse_args()
    return reset_user_password(args.email)


if __name__ == "__main__":
    sys.exit(main())
