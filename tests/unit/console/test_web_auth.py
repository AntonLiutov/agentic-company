from agentic_company.console.web.auth import (
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
    encrypted = encrypt_secret("sk-demo-secret", app_secret="local-secret")

    assert encrypted
    assert "sk-demo-secret" not in encrypted
    assert decrypt_secret(encrypted, app_secret="local-secret") == "sk-demo-secret"
