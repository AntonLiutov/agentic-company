import json
from pathlib import Path

import pytest

from agentic_company.agents.architecture.agent import ArchitectAgent
from agentic_company.agents.business_analysis.agent import BusinessAnalystAgent
from agentic_company.agents.deployment.agent import AzureDeploymentAgent
from agentic_company.agents.fullstack.agent import FullstackAgent
from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.head.agent import HeadAgent
from agentic_company.agents.project_manager.agent import ProjectManagerAgent
from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.agents.registry import active_agents, agent_by_id
from agentic_company.agents.team_lead.agent import TeamLeadAgent
from agentic_company.platform.agent_contracts import append_downstream_response
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.messages import AgentMessage, AgentMessageStore
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.run_trace import load_run_events
from agentic_company.platform.state import initial_delivery_state


class FakeRunner:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.run_dirs: list[Path] = []

    def run(self, run_dir: Path) -> AgentRunResult:
        self.run_dirs.append(run_dir)
        return self.result


def direct_executor() -> DirectSpecialistAgentExecutor:
    return DirectSpecialistAgentExecutor()


def test_agent_registry_lists_first_class_delivery_agents():
    descriptors = active_agents()
    agent_ids = [descriptor.agent_id for descriptor in descriptors]

    assert agent_ids == [
        "head-agent",
        "business-analyst-agent",
        "architect-agent",
        "project-manager-agent",
        "team-lead-agent",
        "fullstack-agent",
        "qa-agent",
        "deployment-agent",
        "documentation-handoff-agent",
    ]
    assert agent_by_id("head-agent").runtime == "L4 LangGraph Agent Executor"
    assert (
        agent_by_id("business-analyst-agent").runtime
        == "L4 LangGraph Agent Executor + L6 Codex Business Analyst"
    )
    assert (
        agent_by_id("architect-agent").runtime == "L4 LangGraph Agent Executor + L6 Codex Architect"
    )
    assert (
        agent_by_id("project-manager-agent").runtime
        == "L4 LangGraph Agent Executor + L6 Codex Project Manager"
    )
    assert agent_by_id("team-lead-agent").runtime == "L4 LangGraph Agent Executor"
    assert agent_by_id("qa-agent").runtime == "L4 LangGraph Agent Executor + L6 Codex QA Agent"
    assert (
        agent_by_id("deployment-agent").runtime
        == "L4 LangGraph Agent Executor + L6 Codex Deployment Agent"
    )
    assert (
        agent_by_id("documentation-handoff-agent").runtime
        == "L4 LangGraph Agent Executor + L6 Codex Handoff Agent"
    )
    with pytest.raises(KeyError):
        agent_by_id("missing-agent")


def test_team_lead_agent_uses_base_capabilities_and_communication_policy():
    agent = TeamLeadAgent()

    assert agent.agent_id == "team-lead-agent"
    assert agent.can_use_tool("send_message")
    assert agent.can_use_tool("delegate_to_agent")
    assert agent.can_use_tool("codex_review")
    assert agent.can_use_tool("inspect_sprint_status")
    assert not agent.can_use_tool("assign_next_feature")
    assert not agent.can_use_tool("codex_exec")
    assert agent.can_message("qa-agent", intent="request_qa")
    assert agent.can_message("fullstack-agent", intent="delegate_feature")
    assert not agent.can_message("qa-agent", intent="random_chat")
    assert not agent.can_message("fullstack-agent", intent="request_qa")
    assert not agent.can_message("business-analyst-agent", intent="delegate_feature")


def test_head_agent_uses_base_capabilities_and_communication_policy():
    agent = HeadAgent()

    assert agent.agent_id == "head-agent"
    assert agent.can_use_tool("send_message")
    assert agent.can_use_tool("delegate_to_agent")
    assert agent.can_use_tool("codex_review")
    assert agent.can_use_tool("inspect_delivery_status")
    assert agent.can_use_tool("run_business_analyst")
    assert agent.can_use_tool("run_architect")
    assert not agent.can_use_tool("codex_exec")
    assert agent.can_message("business-analyst-agent", intent="request_business_analysis")
    assert agent.can_message("architect-agent", intent="request_architecture")
    assert not agent.can_message("team-lead-agent", intent="delegate_feature")


def test_business_analyst_agent_uses_scoped_codex_capability():
    agent = BusinessAnalystAgent(
        runner=FakeRunner(AgentRunResult("business-analyst-agent", "done", [], "")),
        agent_executor=direct_executor(),
    )

    assert agent.agent_id == "business-analyst-agent"
    assert agent.can_use_tool("codex_exec")
    assert not agent.can_use_tool("send_message")
    assert agent.capabilities.can_use_codex
    assert agent.can_message("team-lead-agent", intent="agent_response")
    assert agent.can_message("project-manager-agent", intent="request_clarification")
    assert not agent.can_message("qa-agent", intent="agent_response")


def test_architect_agent_uses_scoped_codex_capability():
    agent = ArchitectAgent(
        runner=FakeRunner(AgentRunResult("architect-agent", "done", [], "")),
        agent_executor=direct_executor(),
    )

    assert agent.agent_id == "architect-agent"
    assert agent.can_use_tool("codex_exec")
    assert not agent.can_use_tool("send_message")
    assert agent.capabilities.can_use_codex
    assert agent.can_message("team-lead-agent", intent="agent_response")
    assert agent.can_message("project-manager-agent", intent="request_clarification")
    assert not agent.can_message("qa-agent", intent="agent_response")


def test_project_manager_agent_uses_scoped_codex_capability():
    agent = ProjectManagerAgent(
        runner=FakeRunner(AgentRunResult("project-manager-agent", "done", [], "")),
        agent_executor=direct_executor(),
    )

    assert agent.agent_id == "project-manager-agent"
    assert agent.can_use_tool("codex_exec")
    assert not agent.can_use_tool("send_message")
    assert agent.capabilities.can_use_codex
    assert agent.can_message("team-lead-agent", intent="agent_response")
    assert agent.can_message("architect-agent", intent="request_clarification")
    assert not agent.can_message("qa-agent", intent="agent_response")


def test_specialist_agents_use_base_capabilities_and_scoped_communication():
    agents = [
        FullstackAgent(
            runner=FakeRunner(AgentRunResult("fullstack-agent", "done", [], "")),
            agent_executor=direct_executor(),
        ),
        QualityAgent(
            runner=FakeRunner(AgentRunResult("qa-agent", "qa_passed", [], "")),
            agent_executor=direct_executor(),
        ),
        AzureDeploymentAgent(
            runner=FakeRunner(AgentRunResult("deployment-agent", "deployment_deployed", [], "")),
            agent_executor=direct_executor(),
        ),
        HandoffAgent(
            runner=FakeRunner(AgentRunResult("handoff-codex-agent", "handoff_ready", [], "")),
            agent_executor=direct_executor(),
        ),
    ]

    for agent in agents:
        assert agent.can_use_tool("send_message")
        assert agent.can_use_tool("codex_exec")
        assert agent.capabilities.can_use_codex
        assert agent.can_message("team-lead-agent", intent="report_status")
        assert agent.can_message("business-analyst-agent", intent="agent_response")
        assert agent.can_message("project-manager-agent", intent="escalate_blocker")
        assert agent.can_message("team-lead-agent", intent="request_clarification")
        assert not agent.can_message("qa-agent", intent="report_status")

    assert HandoffAgent(
        runner=FakeRunner(AgentRunResult("handoff-codex-agent", "handoff_ready", [], "")),
        agent_executor=direct_executor(),
    ).can_message("team-lead-agent", intent="agent_response")


def test_downstream_response_returns_to_requesting_upstream_agent(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    parent = AgentMessageStore(run_dir).append(
        AgentMessage(
            message_id="msg-ba-to-qa",
            from_agent="business-analyst-agent",
            to_agent="qa-agent",
            intent="request_qa",
            content="Validate this from a BA-owned workflow.",
            correlation_id="BA-1",
        )
    )
    state["agent_call_message_id"] = parent.message_id
    state["agent_call_correlation_id"] = "BA-1"

    append_downstream_response(
        state,
        from_agent="qa-agent",
        result=AgentRunResult(
            agent_id="qa-agent",
            status="qa_passed",
            output_artifacts=["qa/results.json"],
            summary="QA passed.",
        ),
    )

    responses = AgentMessageStore(run_dir).read(
        from_agent="qa-agent",
        to_agent="business-analyst-agent",
        intent="agent_response",
    )
    assert len(responses) == 1
    assert responses[0].content == "QA passed."
    assert responses[0].correlation_id == "BA-1"
    assert responses[0].parent_message_id == "msg-ba-to-qa"


def test_downstream_response_does_not_guess_upstream_agent(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)

    append_downstream_response(
        state,
        from_agent="qa-agent",
        result=AgentRunResult(
            agent_id="qa-agent",
            status="qa_passed",
            output_artifacts=["qa/results.json"],
            summary="QA passed.",
        ),
    )

    assert AgentMessageStore(run_dir).read(intent="agent_response") == []


def test_downstream_response_records_codex_thread_without_parent_message(tmp_path):
    run_dir = tmp_path / "run"
    state = initial_delivery_state(run_id="run", run_dir=run_dir)

    append_downstream_response(
        state,
        from_agent="fullstack-agent",
        result=AgentRunResult(
            agent_id="fullstack-agent",
            status="codex_completed",
            output_artifacts=["07-execution-summary.md"],
            summary="done",
            codex_thread_id="thread-f1",
        ),
    )

    assert state["codex_threads_by_agent"] == {"fullstack-agent": "thread-f1"}


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

    result = FullstackAgent(runner=runner, agent_executor=direct_executor()).run(state)

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


def test_business_analyst_agent_maps_analysis_result_to_delivery_state(tmp_path):
    run_dir = tmp_path / "run"
    requirements_path = run_dir / "00-requirements.md"
    run_dir.mkdir()
    requirements_path.write_text("Build a task tracker.\n", encoding="utf-8")
    state = initial_delivery_state(
        run_id="run",
        run_dir=run_dir,
        requirements_path=requirements_path,
    )
    runner = FakeRunner(
        AgentRunResult(
            agent_id="business-analyst-agent",
            status="business_analysis_completed",
            output_artifacts=[
                "upstream-planning/business-analysis.md",
                "upstream-planning/business-analysis.json",
            ],
            summary="Business analysis complete.",
        )
    )

    result = BusinessAnalystAgent(runner=runner, agent_executor=direct_executor()).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "business_analysis"
    assert result["status"] == "business_analysis_completed"
    assert result["completed_nodes"] == ["business_analyst"]
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "upstream-planning/business-analysis.md",
        "upstream-planning/business-analysis.json",
    ]


def test_architect_agent_maps_architecture_result_to_delivery_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "upstream-planning").mkdir()
    (run_dir / "upstream-planning" / "business-analysis.md").write_text("# BA\n", encoding="utf-8")
    (run_dir / "upstream-planning" / "business-analysis.json").write_text(
        "{}",
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="architect-agent",
            status="architecture_completed",
            output_artifacts=[
                "upstream-planning/architecture.md",
                "upstream-planning/architecture.json",
                "upstream-planning/architecture.mmd",
            ],
            summary="Architecture complete.",
        )
    )

    result = ArchitectAgent(runner=runner, agent_executor=direct_executor()).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "architecture"
    assert result["status"] == "architecture_completed"
    assert result["completed_nodes"] == ["architecture"]
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "upstream-planning/architecture.md",
        "upstream-planning/architecture.json",
        "upstream-planning/architecture.mmd",
    ]


def test_project_manager_agent_maps_planning_result_to_delivery_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    planning_dir = run_dir / "upstream-planning"
    planning_dir.mkdir()
    for artifact in (
        "business-analysis.md",
        "business-analysis.json",
        "architecture.md",
        "architecture.json",
    ):
        (planning_dir / artifact).write_text("{}\n", encoding="utf-8")
    project_management_dir = planning_dir / "project-management"
    project_management_dir.mkdir()
    (project_management_dir / "candidate-feature-queue.json").write_text(
        json.dumps(
            [
                {
                    "id": "F1",
                    "title": "Create and list tasks",
                    "description": "Implement the first feature.",
                    "acceptance_criteria": ["Task can be created."],
                    "dependencies": [],
                    "qa_notes": ["Check create/list."],
                    "deployment_notes": ["Deploy at sprint end."],
                    "delivery_order": 1,
                    "status": "pending",
                    "sprint_id": "sprint-01",
                    "source_refs": ["F1"],
                    "suggested_owner_agent": "fullstack-agent",
                }
            ]
        ),
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="project-manager-agent",
            status="project_management_completed",
            output_artifacts=[
                "upstream-planning/project-management/release-plan.md",
                "upstream-planning/project-management/release-plan.json",
                "upstream-planning/project-management/candidate-feature-queue.json",
                "upstream-planning/project-management/roadmap.csv",
            ],
            summary="Project management complete.",
        )
    )

    result = ProjectManagerAgent(runner=runner, agent_executor=direct_executor()).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "project_management"
    assert result["status"] == "project_management_completed"
    assert result["completed_nodes"] == ["project_management"]
    assert result["candidate_feature_queue"][0]["id"] == "F1"
    assert result["work_items"][0]["id"] == "F1"
    assert result["work_board"]["items"][0]["item_id"] == "F1"
    assert result["work_board"]["items"][0]["lane"] == "todo"
    planned_events = [
        event for event in load_run_events(run_dir) if event.event_type == "work_item_planned"
    ]
    assert planned_events[0].work_item_id == "F1"
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "upstream-planning/project-management/release-plan.md",
        "upstream-planning/project-management/release-plan.json",
        "upstream-planning/project-management/candidate-feature-queue.json",
        "upstream-planning/project-management/roadmap.csv",
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

    result = QualityAgent(runner=runner, agent_executor=direct_executor()).run(state)

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
    (run_dir / "deployment").mkdir(parents=True)
    (run_dir / "deployment" / "result.json").write_text(
        '{"status":"deployed","public_urls":["https://app.example.com"]}',
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="run", run_dir=run_dir)
    runner = FakeRunner(
        AgentRunResult(
            agent_id="deployment-agent",
            status="deployment_deployed",
            output_artifacts=["13-deployment-summary.md"],
            summary="deployed",
        )
    )

    result = AzureDeploymentAgent(runner=runner, agent_executor=direct_executor()).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "deployment"
    assert result["status"] == "deployment_deployed"
    assert result["deployment_status"] == "deployed"
    assert result["public_url"] == "https://app.example.com"
    assert result["completed_nodes"] == ["deployment"]


def test_handoff_agent_maps_handoff_status_and_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    state = initial_delivery_state(
        run_id="run",
        run_dir=run_dir,
        target_project_dir=target_dir,
    )
    state["handoff_scope"] = "sprint_handoff"
    state["handoff_sprint_id"] = "sprint-01"
    runner = FakeRunner(
        AgentRunResult(
            agent_id="handoff-codex-agent",
            status="handoff_ready",
            output_artifacts=[
                "handoff/sprints/sprint-01/release-report.html",
            ],
            summary="ready",
        )
    )

    result = HandoffAgent(runner=runner, agent_executor=direct_executor()).run(state)

    assert runner.run_dirs == [run_dir]
    assert result["stage"] == "handoff"
    assert result["status"] == "handoff_ready"
    assert result["completed_nodes"] == ["handoff"]
    assert result["artifacts"] == [
        {
            "path": "handoff/sprints/sprint-01/release-report.html",
            "kind": "handoff",
            "owner_agent": "handoff-codex-agent",
            "visibility": "user",
        },
    ]
