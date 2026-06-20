"""Unit tests for the shared run-stop signal."""

from __future__ import annotations

from pathlib import Path

from redis.exceptions import ConnectionError as RedisConnectionError

from agentic_company.platform.db.runtime_db import run_stop_requested


def test_run_stop_requested_false_without_signal(tmp_path: Path):
    # Unknown run, no stop file, no flags -> fail-open to False.
    assert run_stop_requested("unknown-run-xyz", tmp_path) is False


def test_run_stop_requested_true_with_stop_file(tmp_path: Path):
    (tmp_path / ".stop-requested").write_text("1", encoding="utf-8")
    assert run_stop_requested("unknown-run-xyz", tmp_path) is True


def test_run_stop_requested_treats_runtime_cache_errors_as_no_signal(tmp_path: Path, monkeypatch):
    class FailingCache:
        def stop_requested(self, _run_id: str) -> bool:
            raise RedisConnectionError("redis unavailable")

    monkeypatch.setattr(
        "agentic_company.platform.db.runtime_cache.runtime_cache_from_env",
        lambda: FailingCache(),
    )

    assert run_stop_requested("unknown-run-xyz", tmp_path) is False


def test_run_stop_requested_treats_durable_db_errors_as_no_signal(
    tmp_path: Path,
    monkeypatch,
):
    def raise_db_error(_run_id: str) -> bool:
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("agentic_company.platform.db.runtime_db.stop_requested", raise_db_error)
    monkeypatch.delenv("AGENTIC_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    assert run_stop_requested("run-1", tmp_path) is False
