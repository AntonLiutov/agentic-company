import json
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


def test_fullstack_agent_graph_runs_multi_service_features_sequentially(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    feature_queue = [
        {
            "id": "F1",
            "title": "Create and list tasks",
            "acceptance_criteria": ["API can create a task"],
            "delivery_order": 1,
        },
        {
            "id": "F2",
            "title": "Mark tasks done",
            "acceptance_criteria": ["API can mark a task as done"],
            "delivery_order": 2,
        },
    ]
    _write_execution_request(run_dir, feature_queue)
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["project_archetype"] = "api-web-compose"
    state["feature_queue"] = feature_queue
    state["active_feature_id"] = "F1"
    runner = RecordingRunner(
        AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["07-execution-summary.md"],
            summary="done",
        )
    )

    result = run_fullstack_agent_graph(state, runner)

    assert runner.active_feature_ids == ["F1"]
    assert result["stage"] == "fullstack"
    assert result["status"] == "fullstack_feature_implemented"
    assert result["completed_feature_ids"] == []
    assert result["active_feature_id"] == "F1"
    assert result["feature_statuses"] == {"F1": "implemented"}
    assert result["blockers"] == []


def test_fullstack_agent_graph_stops_feature_iteration_on_failed_feature(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    feature_queue = [
        {"id": "F1", "title": "Create tasks", "acceptance_criteria": [], "delivery_order": 1},
        {"id": "F2", "title": "Mark done", "acceptance_criteria": [], "delivery_order": 2},
    ]
    _write_execution_request(run_dir, feature_queue)
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["project_archetype"] = "api-web-compose"
    state["feature_queue"] = feature_queue
    runner = RecordingRunner(
        AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_failed",
            output_artifacts=["07-execution-summary-F1.md"],
            summary="failed",
        )
    )

    result = run_fullstack_agent_graph(state, runner)

    assert runner.active_feature_ids == ["F1"]
    assert result["status"] == "fullstack_feature_failed"
    assert result["completed_feature_ids"] == []
    assert result["active_feature_id"] == "F1"
    assert result["blockers"] == ["Fullstack feature F1 did not complete successfully."]


def test_fullstack_agent_graph_includes_feature_fix_request_on_repair(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    feature_queue = [
        {"id": "F1", "title": "Create tasks", "acceptance_criteria": [], "delivery_order": 1},
    ]
    _write_execution_request(run_dir, feature_queue)
    (run_dir / "10-fix-request-F1.md").write_text("# Fix Request\n", encoding="utf-8")
    (run_dir / "10-fix-request-F1.json").write_text("{}\n", encoding="utf-8")
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["project_archetype"] = "api-web-compose"
    state["feature_queue"] = feature_queue
    state["active_feature_id"] = "F1"
    runner = RecordingRunner()

    run_fullstack_agent_graph(state, runner)

    payload = runner.payloads[-1]
    assert "10-fix-request-F1.md" in payload["input_artifacts"]
    assert "10-fix-request-F1.json" in payload["input_artifacts"]
    assert any("This is a repair run" in item for item in payload["instructions"])


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
        self.active_feature_ids: list[str | None] = []
        self.payloads: list[dict[str, object]] = []

    def run(self, run_dir: Path) -> AgentRunResult:
        self.run_dirs.append(run_dir)
        request = run_dir / "06-execution-request.json"
        if request.exists():
            payload = json.loads(request.read_text(encoding="utf-8"))
            self.payloads.append(payload)
            active_feature = payload.get("active_feature")
            self.active_feature_ids.append(active_feature.get("id") if active_feature else None)
        return self.result


def _write_execution_request(
    run_dir: Path,
    feature_queue: list[dict[str, object]],
) -> None:
    (run_dir / "06-execution-request.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.5",
                "target_project_dir": str(run_dir / "generated-project"),
                "input_artifacts": ["05-implementation-brief.md"],
                "expected_outputs": ["api/app.py", "web/app.py"],
                "instructions": ["Build the active feature."],
                "constraints": ["Keep names stable."],
                "project_archetype": "api-web-compose",
                "feature_queue": feature_queue,
                "active_feature": feature_queue[0],
                "completed_feature_ids": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
