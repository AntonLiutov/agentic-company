"""Runtime profile + environment preflight (`agentic-doctor`).

``AGENTIC_RUNTIME_PROFILE`` selects how strict the host must be before live delivery:

- ``local`` (default): developer-friendly; a failed required check only warns.
- ``vm_mvp``: a shared, multi-user host (the Azure VM). The required services —
  PostgreSQL, the GitHub CLI, and a Codex auth credential matching the auth mode —
  must be ready, or the doctor exits non-zero so a broken host is caught before a
  user starts (and pays for) a run.

The checks are intentionally dependency-light (TCP reachability + presence), so the
doctor never hangs and needs no DB driver to report a down database.
"""

from __future__ import annotations

import os
import shutil
import socket
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class RuntimeProfile(StrEnum):
    LOCAL = "local"
    VM_MVP = "vm_mvp"


def current_profile() -> RuntimeProfile:
    """The active profile from ``AGENTIC_RUNTIME_PROFILE`` (defaults to ``local``)."""
    raw = os.getenv("AGENTIC_RUNTIME_PROFILE", "").strip().lower()
    try:
        return RuntimeProfile(raw) if raw else RuntimeProfile.LOCAL
    except ValueError:
        return RuntimeProfile.LOCAL


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    required: bool
    detail: str


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url)
    return (parsed.hostname or "127.0.0.1"), (parsed.port or default_port)


def _db_check() -> Check:
    url = os.getenv("AGENTIC_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not url:
        return Check("PostgreSQL", False, True, "AGENTIC_DATABASE_URL is not set.")
    host, port = _host_port(url, 5432)
    ok = _tcp_reachable(host, port)
    return Check("PostgreSQL", ok, True, f"{host}:{port} {'reachable' if ok else 'UNREACHABLE'}")


def _redis_check() -> Check:
    url = os.getenv("AGENTIC_REDIS_URL", "").strip() or os.getenv("REDIS_URL", "").strip()
    if not url:
        return Check("Redis", True, False, "not configured (optional; stop stays DB-authoritative)")
    host, port = _host_port(url, 6379)
    ok = _tcp_reachable(host, port)
    return Check(
        "Redis", ok, False, f"{host}:{port} {'reachable' if ok else 'unreachable (optional)'}"
    )


def _gh_check() -> Check:
    ok = bool(shutil.which("gh"))
    return Check(
        "GitHub CLI", ok, True, "gh found" if ok else "gh not on PATH (GitHub delivery needs it)"
    )


def _codex_auth_check() -> Check:
    mode = os.getenv("AGENTIC_CODEX_AUTH_MODE", "api_key").strip().lower()
    if mode in {"chatgpt_service", "user_chatgpt"}:
        home = (
            os.getenv("CODEX_HOME", "").strip() or os.getenv("AGENTIC_CODEX_AUTH_ROOT", "").strip()
        )
        return Check(
            "Codex auth (chatgpt)",
            bool(home),
            True,
            "CODEX_HOME set" if home else "CODEX_HOME / auth root not set for chatgpt auth",
        )
    ok = bool(os.getenv("CODEX_API_KEY", "").strip())
    return Check(
        "Codex auth (api_key)",
        ok,
        True,
        "CODEX_API_KEY set" if ok else "CODEX_API_KEY not set for api_key auth",
    )


def preflight_checks() -> list[Check]:
    """All host readiness checks (PostgreSQL, Redis, GitHub CLI, Codex auth)."""
    return [_db_check(), _redis_check(), _gh_check(), _codex_auth_check()]


def gating_failures(checks: list[Check] | None = None) -> list[Check]:
    """Required checks that are failing (block live delivery under ``vm_mvp``)."""
    return [
        c for c in (checks if checks is not None else preflight_checks()) if c.required and not c.ok
    ]


def main() -> int:
    """`agentic-doctor` CLI: print the preflight, fail non-zero under vm_mvp if not ready."""
    profile = current_profile()
    checks = preflight_checks()
    print(f"ADL runtime profile: {profile.value}")
    for c in checks:
        mark = "OK  " if c.ok else ("!!  " if c.required else "--  ")
        tag = "" if c.ok else (" [required]" if c.required else " [optional]")
        print(f"  {mark}{c.name}{tag}: {c.detail}")
    failures = gating_failures(checks)
    if not failures:
        print("\nAll required checks passed.")
        return 0
    if profile is RuntimeProfile.VM_MVP:
        print(
            f"\nFAIL: {len(failures)} required check(s) failed — "
            "vm_mvp is NOT ready for live delivery."
        )
        return 1
    print(f"\nWARN: {len(failures)} required check(s) failed (local profile — advisory).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
