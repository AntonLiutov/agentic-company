import agentic_company.platform.runtime_profile as rp
from agentic_company.platform.runtime_profile import (
    RuntimeProfile,
    current_profile,
    gating_failures,
)


def test_current_profile_defaults_and_parses(monkeypatch):
    monkeypatch.delenv("AGENTIC_RUNTIME_PROFILE", raising=False)
    assert current_profile() is RuntimeProfile.LOCAL
    monkeypatch.setenv("AGENTIC_RUNTIME_PROFILE", "vm_mvp")
    assert current_profile() is RuntimeProfile.VM_MVP
    monkeypatch.setenv("AGENTIC_RUNTIME_PROFILE", "nonsense")  # unknown -> local, never crash
    assert current_profile() is RuntimeProfile.LOCAL


def test_preflight_marks_db_gh_codex_required_redis_optional(monkeypatch):
    monkeypatch.setenv("AGENTIC_DATABASE_URL", "postgresql://u@127.0.0.1:54329/db")
    monkeypatch.delenv("AGENTIC_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_MODE", "api_key")
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setattr(rp, "_tcp_reachable", lambda host, port, timeout=3.0: True)
    monkeypatch.setattr(rp.shutil, "which", lambda name: "/usr/bin/gh")

    checks = {c.name: c for c in rp.preflight_checks()}
    assert checks["PostgreSQL"].required and checks["PostgreSQL"].ok
    assert checks["GitHub CLI"].required and checks["GitHub CLI"].ok
    assert checks["Codex auth (api_key)"].required and checks["Codex auth (api_key)"].ok
    assert checks["Redis"].required is False  # optional: stop stays DB-authoritative
    assert gating_failures(list(checks.values())) == []


def test_vm_mvp_doctor_fails_when_a_required_check_is_down(monkeypatch):
    monkeypatch.setenv("AGENTIC_RUNTIME_PROFILE", "vm_mvp")
    monkeypatch.setenv("AGENTIC_DATABASE_URL", "postgresql://u@127.0.0.1:5999/db")
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_MODE", "api_key")
    monkeypatch.setenv("CODEX_API_KEY", "x")
    monkeypatch.setattr(rp, "_tcp_reachable", lambda host, port, timeout=3.0: False)  # DB down
    monkeypatch.setattr(rp.shutil, "which", lambda name: "/usr/bin/gh")
    assert rp.main() == 1  # vm_mvp gates live delivery on required readiness


def test_local_doctor_is_advisory_and_never_blocks(monkeypatch):
    monkeypatch.setenv("AGENTIC_RUNTIME_PROFILE", "local")
    monkeypatch.delenv("AGENTIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_MODE", "api_key")
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setattr(rp.shutil, "which", lambda name: None)
    assert rp.main() == 0  # local profile warns but exits 0
