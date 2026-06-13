import pytest

from agentic_company.console.web.auth import (
    SecretEncryptionUnavailable,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    mask_secret,
    verify_password,
)


def test_password_hash_verify_and_rejects_wrong_password():
    encoded = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)


def test_mask_secret_never_exposes_full_value():
    assert mask_secret("sk-1234567890abcdef") == "sk-1234...cdef"


def test_encrypt_secret_roundtrip_with_app_secret():
    app_secret = "local-secret-0123456789"  # >= 16 chars

    encrypted = encrypt_secret("sk-demo-secret", app_secret=app_secret)

    assert encrypted
    assert "sk-demo-secret" not in encrypted
    assert decrypt_secret(encrypted, app_secret=app_secret) == "sk-demo-secret"


def test_encrypt_secret_fails_closed_without_strong_key(monkeypatch):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    with pytest.raises(SecretEncryptionUnavailable):
        encrypt_secret("sk-demo-secret", app_secret="")
    with pytest.raises(SecretEncryptionUnavailable):
        encrypt_secret("sk-demo-secret", app_secret="too-short")
