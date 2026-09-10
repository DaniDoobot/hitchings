"""Administrative CLI script to create portal users (Bloque 8C.1).

Usage:
    python -m scripts.create_user --email usuario@example.com --display-name "Nombre Usuario"
    # Interactively prompts for hidden password and confirmation via getpass.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.core.security import hash_password, validate_password_policy
from app.db.session import SessionLocal
from app.models.user import User


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Creación administrativa de usuarios privados para el portal HITCHINGS."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Dirección de correo electrónico del usuario (se normalizará a minúsculas).",
    )
    parser.add_argument(
        "--display-name",
        required=True,
        help="Nombre completo o identificador para mostrar en el portal.",
    )
    parser.add_argument(
        "--password",
        required=False,
        default=None,
        help="Contraseña en texto plano (opcional; si se omite se solicitará de forma segura por getpass).",
    )
    parser.add_argument(
        "--role",
        required=False,
        default="user",
        choices=["user", "admin"],
        help="Rol de autorización del usuario ('user' o 'admin'). Por defecto: 'user'.",
    )

    args = parser.parse_args()
    normalized_email = args.email.strip().lower()
    display_name = args.display_name.strip()
    role = args.role.strip().lower()

    if not normalized_email or "@" not in normalized_email:
        print(f"Error: La dirección de correo '{args.email}' no es válida.", file=sys.stderr)
        return 1

    if not display_name:
        print("Error: El nombre para mostrar no puede estar vacío.", file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        # Idempotency check: refuse to duplicate or overwrite silently
        existing = db.query(User).filter(User.email == normalized_email).first()
        if existing:
            print(
                f"Error: Ya existe un usuario registrado con el email '{normalized_email}' (ID: {existing.id}). "
                "No se permite duplicar ni sobrescribir cuentas existentes.",
                file=sys.stderr,
            )
            return 1

        password = args.password
        if not password:
            print(f"Creando usuario para: {normalized_email} ({display_name})")
            p1 = getpass.getpass("Password: ")
            if not p1:
                print("Error: La contraseña no puede estar vacía.", file=sys.stderr)
                return 1
            p2 = getpass.getpass("Confirm password: ")
            if p1 != p2:
                print("Error: Las contraseñas introducidas no coinciden.", file=sys.stderr)
                return 1
            password = p1

        valid, err_msg = validate_password_policy(password)
        if not valid:
            print(f"Error: {err_msg}", file=sys.stderr)
            return 1

        pw_hash = hash_password(password)
        new_user = User(
            email=normalized_email,
            display_name=display_name,
            password_hash=pw_hash,
            role=role,
            is_active=True,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        print("Usuario creado con éxito:")
        print(f"  ID:           {new_user.id}")
        print(f"  Email:        {new_user.email}")
        print(f"  Display Name: {new_user.display_name}")
        print(f"  Rol:          {new_user.role}")
        print(f"  Activo:       {new_user.is_active}")
        return 0

    except Exception as exc:
        db.rollback()
        print(f"Error inesperado al crear el usuario: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
