from agentic_company.agents.quality.graph import (
    QUALITY_AGENT_GRAPH_NODE_ORDER,
    build_quality_agent_graph,
    render_quality_agent_graph_mermaid,
)
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_quality_agent_graph_represents_codex_owned_contract():
    assert QUALITY_AGENT_GRAPH_NODE_ORDER == (
        "prepare_context",
        "codex_quality_execution",
        "parse_quality_contract",
        "apply_quality_result",
    )
    mermaid = render_quality_agent_graph_mermaid()

    assert "codex_quality_execution" in mermaid
    assert "parse_quality_contract" in mermaid
    assert "apply_quality_result" in mermaid
    assert "Dependency sync" not in mermaid
    assert "Docker Compose config" not in mermaid


def test_quality_agent_graph_executes_feature_qa(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["feature_queue"] = [
        {
            "id": "F1",
            "title": "Create tasks",
            "acceptance_criteria": ["API creates a task"],
            "delivery_order": 1,
        }
    ]
    state["active_feature_id"] = "F1"
    runner = FakeQaRunner("qa_passed")

    result = build_quality_agent_graph(runner).invoke(
        {"delivery_state": state, "run_dir": str(run_dir)}
    )
    delivery_state = result["delivery_state"]

    assert runner.run_dirs == [run_dir]
    assert delivery_state["stage"] == "qa"
    assert delivery_state["qa_status"] == "passed"
    assert delivery_state["completed_feature_ids"] == ["F1"]
    assert delivery_state["active_feature_id"] is None
    assert delivery_state["status"] == "feature_queue_qa_completed_deployment_ready"


class FakeQaRunner:
    def __init__(self, status: str) -> None:
        self.status = status
        self.run_dirs = []

    def run(self, run_dir):
        self.run_dirs.append(run_dir)
        return AgentRunResult(
            agent_id="qa-codex-agent",
            status=self.status,
            output_artifacts=["08-qa-report-F1.md", "qa/results-F1.json"],
            summary="QA_STATUS: passed",
        )
