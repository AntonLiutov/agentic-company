"""Authentication and provider-secret helpers for the web console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000


@dataclass(frozen=True, slots=True)
class PasswordHash:
    scheme: str
    iterations: int
    salt: str
    digest: str

    def encode(self) -> str:
        return f"{self.scheme}${self.iterations}${self.salt}${self.digest}"


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 and a random salt."""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return PasswordHash(
        scheme=PASSWORD_SCHEME,
        iterations=PASSWORD_ITERATIONS,
        salt=base64.urlsafe_b64encode(salt).decode("ascii"),
        digest=base64.urlsafe_b64encode(digest).decode("ascii"),
    ).encode()


def verify_password(password: str, encoded: str) -> bool:
    """Return whether a password matches a stored PBKDF2 hash."""

    parsed = _parse_password_hash(encoded)
    if parsed is None or parsed.scheme != PASSWORD_SCHEME:
        return False
    try:
        salt = base64.urlsafe_b64decode(parsed.salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(parsed.digest.encode("ascii"))
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        parsed.iterations,
    )
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mask_secret(secret: str) -> str:
    clean = secret.strip()
    if not clean:
        return ""
    if len(clean) <= 10:
        return clean[:2] + "..." + clean[-2:]
    return clean[:7] + "..." + clean[-4:]


def encrypt_secret(secret: str, app_secret: str | None = None) -> str:
    """Encrypt a provider secret with APP_SECRET_KEY when cryptography is installed."""

    key = (app_secret or os.getenv("APP_SECRET_KEY", "")).strip()
    if not key:
        return ""
    try:
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
    except ImportError:
        return ""
    return Fernet(_fernet_key(key)).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, app_secret: str | None = None) -> str:
    key = (app_secret or os.getenv("APP_SECRET_KEY", "")).strip()
    if not key or not ciphertext:
        return ""
    try:
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        return Fernet(_fernet_key(key)).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _fernet_key(app_secret: str) -> bytes:
    digest = hashlib.sha256(app_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _parse_password_hash(encoded: str) -> PasswordHash | None:
    parts = encoded.split("$")
    if len(parts) != 4:
        return None
    scheme, iterations, salt, digest = parts
    try:
        parsed_iterations = int(iterations)
    except ValueError:
        return None
    return PasswordHash(
        scheme=scheme,
        iterations=parsed_iterations,
        salt=salt,
        digest=digest,
    )
