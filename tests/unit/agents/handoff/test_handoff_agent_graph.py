import json

from agentic_company.agents.handoff.graph import build_handoff_agent_graph
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_handoff_agent_graph_creates_execution_request_when_missing(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["handoff_scope"] = "sprint_handoff"
    state["handoff_sprint_id"] = "sprint-01"
    runner = RequestReadingHandoffRunner()

    result = build_handoff_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    assert request["agent_id"] == "documentation-handoff-agent"
    assert request["handoff_scope"] == "sprint_handoff"
    assert request["handoff_sprint_id"] == "sprint-01"
    assert request["expected_outputs"] == ["handoff/sprints/sprint-01/release-report.html"]
    assert runner.agent_ids == ["documentation-handoff-agent"]
    assert result["delivery_state"]["status"] == "handoff_ready"


def test_handoff_agent_graph_records_final_project_report_refs(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["handoff_scope"] = "final_project_report"
    state["handoff_sprint_id"] = ""
    runner = RequestReadingHandoffRunner(
        output_artifacts=[
            "handoff/project/final/release-report.html",
        ]
    )

    result = build_handoff_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    delivery_state = result["delivery_state"]
    assert request["handoff_scope"] == "final_project_report"
    assert request["expected_outputs"] == ["handoff/project/final/release-report.html"]
    assert delivery_state["final_project_report"] == ("handoff/project/final/release-report.html")
    assert delivery_state["final_project_artifacts"] == request["expected_outputs"]


class RequestReadingHandoffRunner:
    def __init__(self, output_artifacts: list[str] | None = None) -> None:
        self.agent_ids: list[str] = []
        self.output_artifacts = output_artifacts or [
            "handoff/sprints/sprint-01/release-report.html",
        ]

    def run(self, run_dir):
        request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
        self.agent_ids.append(request["agent_id"])
        return AgentRunResult(
            agent_id="handoff-codex-agent",
            status="handoff_ready",
            output_artifacts=self.output_artifacts,
            summary="HANDOFF_STATUS: ready",
        )
