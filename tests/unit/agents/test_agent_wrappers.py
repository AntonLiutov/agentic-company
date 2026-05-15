from pathlib import Path

import pytest

from agentic_company.agents.deployment.agent import AzureDeploymentAgent
from agentic_company.agents.fullstack.agent import FullstackAgent
from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.planning.agent import PlanningAgent
from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.agents.registry import active_agents, agent_by_id
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import initial_delivery_state


class FakeRunner:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.run_dirs: list[Path] = []

    def run(self, run_dir: Path) -> AgentRunResult:
        self.run_dirs.append(run_dir)
        return self.result


def test_agent_registry_lists_first_class_delivery_agents():
    descriptors = active_agents()
    agent_ids = [descriptor.agent_id for descriptor in descriptors]

    assert agent_ids == [
        "planning-agent",
        "fullstack-agent",
        "qa-agent",
        "deployment-agent",
        "documentation-handoff-agent",
    ]
    assert agent_by_id("qa-agent").runtime == "L6 Codex QA Agent"
    assert agent_by_id("deployment-agent").runtime == "L6 Codex Deployment Agent"
    assert agent_by_id("documentation-handoff-agent").runtime == "L6 Codex Handoff Agent"
    with pytest.raises(KeyError):
        agent_by_id("missing-agent")


def test_planning_agent_blocks_without_requirements_path(tmp_path):
    state = initial_delivery_state(run_id="run", run_dir=tmp_path / "run")

    result = PlanningAgent().run(state)

    assert result["stage"] == "planning"
    assert result["status"] == "blocked"
    assert result["completed_nodes"] == ["planning"]
    assert result["blockers"] == ["requirements_path is required for planning."]


def test_fullstack_agent_maps_runner_result_to_delivery_state(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["07-execution-summary.md"],
            summary="done",
        )
    )

    result = FullstackAgent(runner=runner).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "fullstack"
    assert result["status"] == "codex_completed"
    assert result["completed_nodes"] == ["fullstack"]
    assert result["artifacts"] == [
        {
            "path": "07-execution-summary.md",
            "kind": "execution",
            "owner_agent": "fullstack-agent",
            "visibility": "user",
        }
    ]


def test_quality_agent_maps_qa_status_and_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="qa-agent",
            status="qa_passed",
            output_artifacts=["08-qa-report.md", "qa/results.json"],
            summary="passed",
        )
    )

    result = QualityAgent(runner=runner).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "qa"
    assert result["status"] == "qa_passed"
    assert result["qa_status"] == "passed"
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "08-qa-report.md",
        "qa/results.json",
    ]


def test_azure_deployment_agent_maps_deployment_status(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="deployment-agent",
            status="deployment_deployed",
            output_artifacts=["13-deployment-summary.md"],
            summary="deployed",
        )
    )

    result = AzureDeploymentAgent(runner=runner).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "deployment"
    assert result["status"] == "deployment_deployed"
    assert result["deployment_status"] == "deployed"
    assert result["completed_nodes"] == ["deployment"]


def test_handoff_agent_maps_handoff_status_and_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    state = initial_delivery_state(
        run_id="run",
        run_dir=run_dir,
        target_project_dir=target_dir,
    )
    runner = FakeRunner(
        AgentRunResult(
            agent_id="handoff-codex-agent",
            status="handoff_ready",
            output_artifacts=[
                "09-handoff-summary.md",
                "handoff/release-report.html",
                "handoff/release-evidence.json",
            ],
            summary="ready",
        )
    )

    result = HandoffAgent(runner=runner).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "handoff"
    assert result["status"] == "handoff_ready"
    assert result["completed_nodes"] == ["handoff"]
    assert result["artifacts"] == [
        {
            "path": "09-handoff-summary.md",
            "kind": "handoff",
            "owner_agent": "handoff-codex-agent",
            "visibility": "user",
        },
        {
            "path": "handoff/release-report.html",
            "kind": "handoff",
            "owner_agent": "handoff-codex-agent",
            "visibility": "user",
        },
        {
            "path": "handoff/release-evidence.json",
            "kind": "handoff",
            "owner_agent": "handoff-codex-agent",
            "visibility": "user",
        },
    ]
