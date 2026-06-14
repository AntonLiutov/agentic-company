from __future__ import annotations

from pathlib import Path

from agentic_company.integrations.codex.worker import (
    CodexWorkerAdapter,
    WorkerBackedAgentRunner,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.ports.worker import WorkRequest


class _FakeCodexRunner:
    def run(self, run_dir: Path) -> AgentRunResult:
        events = run_dir / "events.jsonl"
        events.write_text(
            '{"usage": {"input_tokens": 123, "output_tokens": 45}}\n',
            encoding="utf-8",
        )
        return AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["events.jsonl", "report.md"],
            summary="Done.",
            execution_id="exec-1",
            codex_thread_id="thread-1",
        )


def test_codex_worker_adapter_maps_legacy_result_to_worker_result(tmp_path):
    worker = CodexWorkerAdapter(_FakeCodexRunner())

    result = worker.run(
        WorkRequest(
            run_dir=tmp_path,
            agent_id="fullstack-agent",
            work_item_id="F1",
            stage="fullstack",
        )
    )

    assert result.success is True
    assert result.provider == "codex"
    assert result.worker_session_id == "thread-1"
    assert result.usage is not None
    assert result.usage.input_tokens == 123
    assert result.usage.output_tokens == 45


def test_worker_backed_agent_runner_preserves_agent_run_result(tmp_path):
    runner = WorkerBackedAgentRunner(
        CodexWorkerAdapter(_FakeCodexRunner()),
        agent_id="fullstack-agent",
        stage="fullstack",
    )

    result = runner.run(tmp_path)

    assert result.agent_id == "fullstack-agent"
    assert result.status == "codex_completed"
    assert result.codex_thread_id == "thread-1"
    assert result.output_artifacts == ["events.jsonl", "report.md"]
