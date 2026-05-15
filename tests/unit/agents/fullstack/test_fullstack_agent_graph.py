from pathlib import Path

import pytest

from agentic_company.agents.fullstack.graph import (
    FULLSTACK_AGENT_GRAPH_NODE_ORDER,
    build_fullstack_agent_graph,
    render_fullstack_agent_graph_mermaid,
    run_fullstack_agent_graph,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_fullstack_agent_graph_runs_runner_and_maps_state(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = RecordingRunner(
        AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["07-execution-summary.md", "codex/prompt.md"],
            summary="done",
        )
    )

    result = run_fullstack_agent_graph(state, runner)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "fullstack"
    assert result["status"] == "codex_completed"
    assert result["completed_nodes"] == ["fullstack"]
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "07-execution-summary.md",
        "codex/prompt.md",
    ]


def test_fullstack_agent_graph_exposes_expected_node_order():
    assert FULLSTACK_AGENT_GRAPH_NODE_ORDER == [
        "prepare_context",
        "run_codex",
        "apply_result",
    ]


def test_fullstack_agent_graph_mermaid_includes_internal_nodes():
    mermaid = render_fullstack_agent_graph_mermaid()

    assert "prepare_context" in mermaid
    assert "run_codex" in mermaid
    assert "apply_result" in mermaid


def test_fullstack_agent_graph_requires_nodes():
    with pytest.raises(ValueError, match="requires at least one node"):
        build_fullstack_agent_graph(RecordingRunner(), node_order=[])


class RecordingRunner:
    def __init__(self, result: AgentRunResult | None = None) -> None:
        self.result = result or AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=[],
            summary="done",
        )
        self.run_dirs: list[Path] = []

    def run(self, run_dir: Path) -> AgentRunResult:
        self.run_dirs.append(run_dir)
        return self.result
