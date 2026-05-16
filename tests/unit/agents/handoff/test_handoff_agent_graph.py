import json

from agentic_company.agents.handoff.graph import build_handoff_agent_graph
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_handoff_agent_graph_creates_execution_request_when_missing(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = RequestReadingHandoffRunner()

    result = build_handoff_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    assert request["agent_id"] == "documentation-handoff-agent"
    assert "09-handoff-summary.md" in request["expected_outputs"]
    assert runner.agent_ids == ["documentation-handoff-agent"]
    assert result["delivery_state"]["status"] == "handoff_ready"


class RequestReadingHandoffRunner:
    def __init__(self) -> None:
        self.agent_ids: list[str] = []

    def run(self, run_dir):
        request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
        self.agent_ids.append(request["agent_id"])
        return AgentRunResult(
            agent_id="handoff-codex-agent",
            status="handoff_ready",
            output_artifacts=["09-handoff-summary.md", "handoff/release-evidence.json"],
            summary="HANDOFF_STATUS: ready",
        )
