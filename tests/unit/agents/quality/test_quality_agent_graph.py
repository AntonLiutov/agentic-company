import json

from agentic_company.agents.quality.graph import (
    QUALITY_AGENT_GRAPH_NODE_ORDER,
    build_quality_agent_graph,
    render_quality_agent_graph_mermaid,
)
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_quality_agent_graph_represents_codex_owned_contract():
    assert QUALITY_AGENT_GRAPH_NODE_ORDER == (
        "prepare_context",
        "run_agent_executor",
        "apply_result",
    )
    mermaid = render_quality_agent_graph_mermaid()

    assert "run_agent_executor" in mermaid
    assert "apply_result" in mermaid
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

    result = build_quality_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})
    delivery_state = result["delivery_state"]

    assert runner.run_dirs == [run_dir]
    assert delivery_state["stage"] == "qa"
    assert delivery_state["qa_status"] == "passed"
    assert delivery_state["completed_feature_ids"] == ["F1"]
    assert delivery_state["active_feature_id"] is None
    assert delivery_state["status"] == "feature_queue_qa_completed_deployment_ready"


def test_quality_agent_graph_creates_execution_request_when_missing(tmp_path):
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
    runner = RequestReadingQaRunner()

    build_quality_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    assert request["agent_id"] == "qa-agent"
    assert request["active_feature"]["id"] == "F1"
    assert runner.active_feature_ids == ["F1"]


def test_quality_agent_graph_syncs_active_feature_into_execution_request(tmp_path):
    run_dir = tmp_path / "run"
    request_path = run_dir / "delivery" / "execution-request.json"
    request_path.parent.mkdir(parents=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "agent_id": "fullstack-agent",
                "agent_version": "test",
                "maturity_level": "test",
                "provider": "codex-cli",
                "model": "gpt-test",
                "target_project_dir": str(run_dir / "generated-project"),
                "input_artifacts": [],
                "expected_outputs": [],
                "instructions": [],
                "constraints": [],
                "feature_queue": [{"id": "F1", "title": "Old", "delivery_order": 1}],
                "active_feature": {"id": "F1", "title": "Old", "delivery_order": 1},
                "completed_feature_ids": [],
            }
        ),
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["feature_queue"] = [
        {"id": "F1", "title": "Old", "delivery_order": 1, "sprint_id": "sprint-01"},
        {"id": "F2", "title": "Current", "delivery_order": 2, "sprint_id": "sprint-01"},
    ]
    state["active_feature_id"] = "F2"
    state["completed_feature_ids"] = ["F1"]
    runner = RequestReadingQaRunner()

    result = build_quality_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    synced_request = json.loads(request_path.read_text(encoding="utf-8"))
    assert runner.active_feature_ids == ["F2"]
    assert synced_request["active_feature"]["id"] == "F2"
    assert result["delivery_state"]["feature_statuses"]["F2"] == "qa_passed"


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


class RequestReadingQaRunner:
    def __init__(self) -> None:
        self.active_feature_ids: list[str | None] = []

    def run(self, run_dir):
        request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
        active_feature = request.get("active_feature")
        feature_id = active_feature.get("id") if active_feature else None
        self.active_feature_ids.append(feature_id)
        return AgentRunResult(
            agent_id="qa-codex-agent",
            status="qa_passed",
            output_artifacts=[f"08-qa-report-{feature_id}.md", f"qa/results-{feature_id}.json"],
            summary="QA_STATUS: passed",
        )
