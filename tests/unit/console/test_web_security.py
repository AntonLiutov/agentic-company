"""Tests for console web hardening: CSRF origin guard, login throttling, auth timing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_company.console.web.app import create_app
from agentic_company.console.web.auth import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    verify_password_or_dummy,
)
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.console.web.rate_limit import RateLimiter


def test_rate_limiter_allows_up_to_limit_then_blocks():
    limiter = RateLimiter(max_attempts=3, window_seconds=60)

    assert limiter.allow("k", now=0)
    assert limiter.allow("k", now=1)
    assert limiter.allow("k", now=2)
    assert not limiter.allow("k", now=3)


def test_rate_limiter_window_expiry_frees_capacity():
    limiter = RateLimiter(max_attempts=1, window_seconds=10)

    assert limiter.allow("k", now=0)
    assert not limiter.allow("k", now=5)
    assert limiter.allow("k", now=11)


def test_rate_limiter_reset_and_key_isolation():
    limiter = RateLimiter(max_attempts=1, window_seconds=10)

    assert limiter.allow("a", now=0)
    assert limiter.allow("b", now=0)
    assert not limiter.allow("a", now=1)
    limiter.reset("a")
    assert limiter.allow("a", now=2)


def test_rate_limiter_prunes_stale_keys():
    limiter = RateLimiter(max_attempts=1, window_seconds=10, max_keys=1)

    assert limiter.allow("a", now=0)
    assert limiter.allow("b", now=0)
    # Over the key budget and well past the window: stale keys are evicted.
    assert limiter.allow("c", now=100)
    assert "c" in limiter._hits
    assert "a" not in limiter._hits
    assert "b" not in limiter._hits


def test_verify_password_or_dummy_handles_known_and_unknown():
    encoded = hash_password("correct horse")

    assert verify_password_or_dummy("correct horse", encoded)
    assert not verify_password_or_dummy("wrong", encoded)
    # Unknown account: always returns False, never raises, and exercises a hash.
    assert not verify_password_or_dummy("anything", None)
    assert DUMMY_PASSWORD_HASH.startswith("pbkdf2_sha256$")


def test_cross_origin_write_is_rejected():
    client = TestClient(create_app(ConsoleRepository()))

    response = client.post(
        "/login",
        data={"identifier": "x", "password": "y"},
        headers={"origin": "http://evil.example"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_same_origin_write_reaches_handler():
    client = TestClient(create_app(ConsoleRepository()))

    response = client.post(
        "/login",
        data={"identifier": "x", "password": "y"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )

    # Same-origin request is not blocked; bad credentials yield 401.
    assert response.status_code == 401


def test_login_throttles_after_repeated_failures():
    client = TestClient(create_app(ConsoleRepository()))

    last = None
    for _ in range(11):
        last = client.post(
            "/login",
            data={"identifier": "ghost", "password": "nope"},
            follow_redirects=False,
        )

    assert last is not None
    assert last.status_code == 429


def test_login_throttle_not_bypassable_by_varying_identifier():
    client = TestClient(create_app(ConsoleRepository()))

    for i in range(10):
        client.post(
            "/login",
            data={"identifier": f"user{i}", "password": "nope"},
            follow_redirects=False,
        )
    # Cycling usernames from the same source must not refill the per-IP budget.
    blocked = client.post(
        "/login",
        data={"identifier": "yet-another", "password": "nope"},
        follow_redirects=False,
    )

    assert blocked.status_code == 429
