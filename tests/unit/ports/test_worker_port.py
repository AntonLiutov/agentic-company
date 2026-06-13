from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from agentic_company.ports.registry import worker_for_agent
from agentic_company.ports.worker import UsageTotals, WorkRequest, WorkResult


def test_worker_dtos_are_provider_neutral():
    request = WorkRequest(
        run_dir=Path("run-1"),
        agent_id="fullstack-agent",
        work_item_id="F1",
        stage="fullstack",
    )
    usage = UsageTotals(input_tokens=10, output_tokens=5, cached_tokens=2)
    result = WorkResult(
        success=True,
        summary="Done.",
        output_artifacts=["report.md"],
        worker_session_id="session-1",
        provider="test",
        usage=usage,
        status="completed",
    )

    assert request.run_dir == Path("run-1")
    assert result.worker_session_id == "session-1"
    assert result.codex_thread_id == "session-1"
    assert result.usage == usage
    assert "codex_thread_id" not in {field.name for field in fields(WorkResult)}


def test_worker_registry_fails_loud_for_unknown_provider():
    with pytest.raises(ValueError, match="No worker provider is registered"):
        worker_for_agent("fullstack-agent", provider="missing")


def test_worker_registry_fails_loud_for_unknown_codex_agent():
    with pytest.raises(ValueError, match="No Codex worker runner is registered"):
        worker_for_agent("missing-agent", provider="codex")
