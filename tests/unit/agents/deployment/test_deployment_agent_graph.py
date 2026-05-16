import json

from agentic_company.agents.deployment.graph import (
    DEPLOYMENT_AGENT_GRAPH_NODE_ORDER,
    build_deployment_agent_graph,
    render_deployment_agent_graph_mermaid,
)
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


def test_deployment_agent_graph_represents_codex_owned_contract():
    assert DEPLOYMENT_AGENT_GRAPH_NODE_ORDER == (
        "prepare_context",
        "run_agent_executor",
        "apply_result",
    )
    mermaid = render_deployment_agent_graph_mermaid()

    assert "run_agent_executor" in mermaid
    assert "apply_result" in mermaid
    assert "ensure_registry" not in mermaid
    assert "build_and_push_image" not in mermaid
    assert "run_post_deploy_qa" not in mermaid


def test_deployment_agent_graph_maps_deployed_contract(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "deployment").mkdir()
    (run_dir / "deployment" / "result.json").write_text(
        '{"status":"deployed","public_urls":["https://app.example.com"]}',
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeDeploymentRunner("deployment_deployed")

    result = build_deployment_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})
    delivery_state = result["delivery_state"]

    assert runner.run_dirs == [run_dir]
    assert delivery_state["stage"] == "deployment"
    assert delivery_state["status"] == "deployment_deployed"
    assert delivery_state["deployment_status"] == "deployed"
    assert delivery_state["public_url"] == "https://app.example.com"
    assert delivery_state["completed_nodes"] == ["deployment"]

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    assert request["agent_id"] == "deployment-agent"
    assert request["target_project_dir"] == str(run_dir / "generated-project")
    assert request["expected_outputs"] == [
        "deployment/result.json",
        "11-deployment-plan.json",
        "11-deployment-plan.md",
        "12-deployment-request.json",
        "12-deployment-request.md",
        "13-deployment-summary.md",
    ]


def test_deployment_agent_graph_writes_active_feature_request(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "deployment").mkdir()
    (run_dir / "deployment" / "result.json").write_text(
        '{"status":"blocked","public_urls":[]}',
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    state["active_feature_id"] = "DEP1"
    state["feature_queue"] = [
        {
            "id": "DEP1",
            "title": "Azure dev deploy",
            "suggested_owner_agent": "deployment-agent",
            "delivery_order": 1,
        }
    ]

    build_deployment_agent_graph(
        FakeDeploymentRunner("deployment_blocked"),
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})

    request = json.loads((run_dir / "delivery" / "execution-request.json").read_text())
    assert request["active_feature"]["id"] == "DEP1"
    assert request["feature_queue"][0]["suggested_owner_agent"] == "deployment-agent"


def test_deployment_agent_graph_normalizes_deployed_without_urls_to_unknown(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "deployment").mkdir()
    (run_dir / "deployment" / "result.json").write_text(
        '{"status":"deployed","public_urls":[]}',
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeDeploymentRunner("deployment_deployed")

    result = build_deployment_agent_graph(
        runner,
        agent_executor=DirectSpecialistAgentExecutor(),
    ).invoke({"delivery_state": state, "run_dir": str(run_dir)})
    delivery_state = result["delivery_state"]

    assert delivery_state["stage"] == "deployment"
    assert delivery_state["status"] == "deployment_unknown"
    assert delivery_state["deployment_status"] == "unknown"
    assert delivery_state["public_url"] is None


class FakeDeploymentRunner:
    def __init__(self, status: str) -> None:
        self.status = status
        self.run_dirs = []

    def run(self, run_dir):
        self.run_dirs.append(run_dir)
        return AgentRunResult(
            agent_id="deployment-codex-agent",
            status=self.status,
            output_artifacts=[
                "deployment/result.json",
                "11-deployment-plan.json",
                "11-deployment-plan.md",
                "12-deployment-request.json",
                "12-deployment-request.md",
                "13-deployment-summary.md",
            ],
            summary="DEPLOYMENT_STATUS: deployed",
        )
