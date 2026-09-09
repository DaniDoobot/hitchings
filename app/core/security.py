"""Security and cryptography utilities: Argon2id password hashing and session tokens (Bloque 8C.1)."""

from __future__ import annotations

import hashlib
import secrets
import logging
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

logger = logging.getLogger(__name__)

# Argon2id password hasher with standard RFC 9106 recommended parameters
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Precomputed dummy hash for constant-time dummy verification on non-existent users
# (prevents timing side-channel attacks for username enumeration)
_DUMMY_HASH: str = _hasher.hash("dummy_constant_time_timing_resistance_password")

# Administrative password policy: reasonable minimal length (Bloque 9A hardening)
MIN_PASSWORD_LENGTH: int = 12


def validate_password_policy(password: str) -> tuple[bool, str | None]:
    """Validate password against baseline policy: minimal length check.
    
    Does not arbitrarily enforce uppercase/symbol/number rules.
    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    if not password:
        return False, "La contraseña no puede estar vacía."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
    return True, None


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id password hash.
    
    Returns True if valid, False otherwise. Never raises on invalid credentials.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception as exc:
        logger.warning("Unexpected error during password verification: %s", exc)
        return False


def dummy_verify_password(password: str) -> None:
    """Perform a dummy verification to equalize execution time when an account is not found."""
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except Exception:
        pass


def generate_session_token() -> str:
    """Generate a cryptographically secure random session token (32 bytes urlsafe)."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Compute the SHA-256 hexadecimal digest of a session token for safe server-side storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
