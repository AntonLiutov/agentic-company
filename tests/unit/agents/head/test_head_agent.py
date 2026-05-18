import json

from agentic_company.agents.head.agent import HeadAgent
from agentic_company.agents.head.executor import build_head_executor_prompt
from agentic_company.agents.head.tools import HeadExecutorResult, HeadToolbox, HeadWorkers
from agentic_company.platform.agent_contracts import append_downstream_response
from agentic_company.platform.codex_review import CodexReviewRequest, CodexReviewResult
from agentic_company.platform.messages import AgentMessageStore
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import (
    DeliveryState,
    initial_delivery_state,
    mark_node_completed,
)
from agentic_company.platform.status_inspector import (
    StatusInspectionRequest,
    StatusInspectionResult,
)


class SequencingHeadExecutor:
    def run(
        self,
        *,
        delivery_state: DeliveryState,
        workers: HeadWorkers,
        max_steps: int,
    ) -> HeadExecutorResult:
        toolbox = HeadToolbox(
            delivery_state=delivery_state,
            workers=workers,
            max_steps=max_steps,
            status_inspector=FakeStatusInspector(scope="delivery"),
        )
        toolbox.run_business_analyst(
            reason="Analyze product intent.",
            message="Create business analysis for Head Agent review.",
        )
        toolbox.run_architect(
            reason="Create architecture from BA artifacts.",
            message="Use BA artifacts and answer Head Agent with architecture risks.",
        )
        toolbox.run_project_manager(
            reason="Create sprint plans from BA and architecture artifacts.",
            message="Use BA and architecture artifacts to create Team Lead-ready sprint plans.",
        )
        toolbox.inspect_delivery_status(reason="Find first sprint target.")
        toolbox.run_team_lead(
            target="sprint-01",
            reason="Execute sprint delivery from PM feature queue.",
            message="Use PM artifacts and deliver the sprint through the delivery team.",
        )
        toolbox.inspect_delivery_status(reason="Confirm delivery completion readiness.")
        toolbox.complete_delivery(reason="BA, Architect, PM, and Team Lead completed.")
        return toolbox.result()


class FakeCodexReviewer:
    def __init__(self, content: str = "Review feedback.") -> None:
        self.content = content
        self.requests: list[CodexReviewRequest] = []

    def run(self, request: CodexReviewRequest) -> CodexReviewResult:
        self.requests.append(request)
        return CodexReviewResult(
            status="reviewed",
            content=self.content,
            artifact_refs=request.artifact_refs,
            summary_artifact="head/codex-review/summary.md",
            prompt_artifact="head/codex-review/prompt.md",
            log_artifact="head/codex-review/execution.log",
            execution_id=request.execution_id,
            codex_thread_id="thread-review",
        )


class FakeStatusInspector:
    def __init__(self, *, scope: str) -> None:
        self.scope = scope
        self.requests: list[StatusInspectionRequest] = []

    def run(self, request: StatusInspectionRequest) -> StatusInspectionResult:
        self.requests.append(request)
        payload = {
            "status": "inspected",
            "scope": self.scope,
            "delivery_status": "ready_to_complete",
            "sprints": [],
            "workers_called": [],
            "gates": {
                "planning_done": True,
                "all_sprints_done": True,
                "deployment_done": False,
                "final_handoff_ready": True,
            },
            "can_complete_delivery": True,
            "status_summary": "Delivery ready to complete.",
            "status_legend": {"ready_to_complete": "All required work is done."},
        }
        return StatusInspectionResult(
            status="inspected",
            payload=payload,
            artifact_refs=[],
            result_artifact="head/status-inspections/status.json",
            summary_artifact="head/status-inspections/summary.md",
            prompt_artifact="head/status-inspections/prompt.md",
            log_artifact="head/status-inspections/execution.log",
            execution_id=request.execution_id,
            codex_thread_id="thread-status",
        )


def test_head_agent_uses_coordinator_capabilities_and_scoped_tools():
    agent = HeadAgent(executor=SequencingHeadExecutor())

    assert agent.agent_id == "head-agent"
    assert agent.can_use_tool("send_message")
    assert agent.can_use_tool("delegate_to_agent")
    assert agent.can_use_tool("run_business_analyst")
    assert agent.can_use_tool("run_architect")
    assert agent.can_use_tool("run_project_manager")
    assert agent.can_use_tool("run_team_lead")
    assert agent.can_use_tool("inspect_delivery_status")
    assert not agent.can_use_tool("block_planning")
    assert not agent.can_use_tool("codex_exec")
    assert agent.can_message("business-analyst-agent", intent="request_business_analysis")
    assert agent.can_message("architect-agent", intent="request_architecture")
    assert agent.can_message("project-manager-agent", intent="request_project_management")
    assert agent.can_message("team-lead-agent", intent="request_sprint_delivery")


def test_head_prompt_exposes_current_specialist_communication_context(tmp_path):
    state = initial_delivery_state(run_id="prompt-test", run_dir=tmp_path)

    prompt = build_head_executor_prompt(delivery_state=state)

    assert "communication_context" in prompt
    assert "business-analyst-agent" in prompt
    assert "architect-agent" in prompt
    assert "project-manager-agent" in prompt
    assert "team-lead-agent" in prompt
    assert "Do not prescribe exact output paths" in prompt
    assert "coordinator_quality_review_policy" in prompt
    assert "keep coordinator review lightweight" in prompt
    assert "Use `codex_review` only when the response is unclear" in prompt
    assert "coordinator_recovery_policy" in prompt
    assert "run_business_analyst" in prompt
    assert "run_architect" in prompt
    assert "run_project_manager" in prompt
    assert "run_team_lead" in prompt
    assert "block_planning" not in prompt
    assert "Scale every specialist assignment to the source request complexity" in prompt
    assert "do not inflate the request into enterprise" in prompt
    assert "do not prescribe long custom deliverable" in prompt
    assert "follow its own contract" in prompt
    assert "Treat deployable access as the default expectation" in prompt
    assert "smallest reasonable Team Lead-consumable release plan" in prompt
    assert "not split work merely to fill sprint or feature counts" in prompt
    assert "Use the exact canonical sprint_id from PM artifacts" in prompt
    assert "do not invent aliases such as Sprint 01, S1" in prompt
    assert "status/evidence readback" in prompt
    assert "Do not let the inspector choose the next tool" in prompt
    assert "failed, blocked, waiting, refused, precondition" in prompt
    assert "rerun the owning downstream tool" in prompt
    assert "call `codex_review` before rerunning the owner" in prompt
    assert "5 repair attempt" in prompt
    assert "Apply coordinator_recovery_policy to Business Analyst" in prompt
    assert "coordinator sanity-checks the response" in prompt
    assert "Do not invent a separate sprint report path" in prompt
    assert "Do not rerun the same completed sprint" in prompt
    assert "`team_lead_sprint_handoff_ready` means the addressed sprint handoff is ready" in prompt
    assert "it does not by itself mean the whole project delivery is complete" in prompt
    assert "call run_team_lead again with the next sprint_id" in prompt
    assert "Never translate one sprint handoff into project completion" in prompt
    assert "release/deployment gates to find the next sprint target generically" in prompt
    assert "Head Agent is a coordinator, not a release auditor" in prompt
    assert "Never require repo URL" in prompt
    assert "CI workflow" in prompt
    assert "optional notes and continue routing" in prompt
    assert "review only against the active PM sprint plan" in prompt
    assert "Head has no normal block authority from Codex Review" in prompt


def test_head_run_team_lead_requires_explicit_sprint_target(tmp_path):
    pm_dir = tmp_path / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "sprint-02-plan.json").write_text(
        json.dumps({"sprint_id": "S2", "ordered_features": ["F2"]}),
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="next-sprint", run_dir=tmp_path)
    state["team_lead_sprint_id"] = "sprint-01"
    state["feature_queue"] = [
        {"id": "F1", "title": "Done", "delivery_order": 1, "sprint_id": "S1"},
        {"id": "F2", "title": "Next", "delivery_order": 2, "sprint_id": "S2"},
    ]
    state["completed_feature_ids"] = ["F1"]
    state["feature_statuses"] = {"F1": "qa_passed"}
    calls: list[DeliveryState] = []

    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: calls.append(state) or state,
        ),
        max_steps=6,
    )

    response = json.loads(
        toolbox.run_team_lead(
            reason="Proceed to Sprint 02.",
            message="Use upstream-planning/project-management/sprint-02-plan.json.",
        )
    )

    assert calls == []
    assert response["status"] == "initialized"
    assert "requires an explicit target sprint id" in response["message"]
    assert toolbox.history[-1]["target"] == "missing-sprint-target"
    assert toolbox.history[-1]["result_status"] == "head_waiting_for_explicit_sprint_target"


def test_head_run_team_lead_sets_explicit_sprint_target_in_state(tmp_path):
    state = initial_delivery_state(run_id="explicit-sprint", run_dir=tmp_path)
    state["team_lead_sprint_id"] = "sprint-01"
    state["feature_queue"] = [
        {"id": "F2", "title": "Next", "delivery_order": 2, "sprint_id": "sprint-02"}
    ]
    seen_targets: list[str] = []

    def team_lead(worker_state: DeliveryState) -> DeliveryState:
        seen_targets.append(str(worker_state.get("team_lead_sprint_id")))
        return mark_node_completed(
            worker_state,
            node_name="team_lead",
            stage="team_lead",
            status="team_lead_sprint_handoff_ready",
        )

    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=team_lead,
        ),
        max_steps=6,
    )

    toolbox.run_team_lead(
        target="sprint-02",
        reason="Proceed to Sprint 02.",
        message="Use upstream-planning/project-management/sprint-02-plan.json.",
    )

    assert seen_targets == ["sprint-02"]
    assert toolbox.history[-1]["target"] == "sprint-02"


def test_complete_delivery_only_marks_complete_without_validating_pending_pm_sprint(tmp_path):
    pm_dir = tmp_path / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "sprint-01-plan.json").write_text(
        json.dumps({"sprint_id": "S1", "ordered_features": ["F1"]}),
        encoding="utf-8",
    )
    (pm_dir / "sprint-02-plan.json").write_text(
        json.dumps({"sprint_id": "S2", "ordered_features": ["F2"]}),
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id="pending-sprint", run_dir=tmp_path)
    state["status"] = "team_lead_sprint_handoff_ready"
    state["stage"] = "team_lead"
    state["feature_queue"] = [
        {"id": "F1", "title": "Done", "delivery_order": 1, "sprint_id": "S1"},
        {"id": "F2", "title": "Pending", "delivery_order": 2, "sprint_id": "S2"},
    ]
    state["completed_feature_ids"] = ["F1"]
    state["feature_statuses"] = {"F1": "qa_passed"}
    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=6,
    )

    response = json.loads(toolbox.complete_delivery(reason="Looks done."))

    assert response["status"] == "head_delivery_completed"
    assert toolbox.history[-1]["result_status"] == "head_delivery_completed"
    assert "head" in toolbox.delivery_state.get("completed_nodes", [])


def test_head_agent_coordinates_business_analyst_and_architect_through_messages(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "00-requirements.md").write_text("Build a task tracker.\n", encoding="utf-8")
    (run_dir / "upstream-planning").mkdir()
    (run_dir / "upstream-planning" / "business-analysis.md").write_text("# BA\n", encoding="utf-8")
    (run_dir / "upstream-planning" / "business-analysis.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (run_dir / "upstream-planning" / "architecture.md").write_text(
        "# Architecture\n",
        encoding="utf-8",
    )
    (run_dir / "upstream-planning" / "architecture.json").write_text(
        "{}",
        encoding="utf-8",
    )
    state = initial_delivery_state(
        run_id="run",
        run_dir=run_dir,
        requirements_path=run_dir / "00-requirements.md",
    )

    def business_analyst(worker_state: DeliveryState) -> DeliveryState:
        append_downstream_response(
            worker_state,
            from_agent="business-analyst-agent",
            result=AgentRunResult(
                agent_id="business-analyst-agent",
                status="business_analysis_completed",
                output_artifacts=[
                    "upstream-planning/business-analysis.md",
                    "upstream-planning/business-analysis.json",
                ],
                summary="BA complete.",
            ),
        )
        return mark_node_completed(
            worker_state,
            node_name="business_analyst",
            stage="business_analysis",
            status="business_analysis_completed",
        )

    def architect(worker_state: DeliveryState) -> DeliveryState:
        append_downstream_response(
            worker_state,
            from_agent="architect-agent",
            result=AgentRunResult(
                agent_id="architect-agent",
                status="architecture_completed",
                output_artifacts=[
                    "upstream-planning/architecture.md",
                    "upstream-planning/architecture.json",
                    "upstream-planning/architecture.mmd",
                ],
                summary="Architecture complete.",
            ),
        )
        return mark_node_completed(
            worker_state,
            node_name="architecture",
            stage="architecture",
            status="architecture_completed",
        )

    def project_manager(worker_state: DeliveryState) -> DeliveryState:
        worker_state["candidate_feature_queue"] = [
            {
                "id": "F1",
                "title": "Create tasks",
                "delivery_order": 1,
                "acceptance_criteria": ["Task can be created."],
                "status": "pending",
            }
        ]
        append_downstream_response(
            worker_state,
            from_agent="project-manager-agent",
            result=AgentRunResult(
                agent_id="project-manager-agent",
                status="project_management_completed",
                output_artifacts=[
                    "upstream-planning/project-management/release-plan.md",
                    "upstream-planning/project-management/release-plan.json",
                    "upstream-planning/project-management/candidate-feature-queue.json",
                ],
                summary="PM complete.",
            ),
        )
        return mark_node_completed(
            worker_state,
            node_name="project_management",
            stage="project_management",
            status="project_management_completed",
        )

    def team_lead(worker_state: DeliveryState) -> DeliveryState:
        worker_state["completed_feature_ids"] = [
            *worker_state.get("completed_feature_ids", []),
            "F1",
        ]
        worker_state["feature_statuses"] = {
            **worker_state.get("feature_statuses", {}),
            "F1": "qa_passed",
        }
        worker_state["artifacts"] = [
            *worker_state.get("artifacts", []),
            {
                "path": "handoff/project/final/release-report.html",
                "kind": "handoff",
                "owner_agent": "handoff-codex-agent",
                "visibility": "user",
            },
        ]
        append_downstream_response(
            worker_state,
            from_agent="team-lead-agent",
            result=AgentRunResult(
                agent_id="team-lead-agent",
                status="team_lead_sprint_handoff_ready",
                output_artifacts=[
                    "team-lead/sprint-01-result.json",
                    "handoff/project/final/release-report.html",
                ],
                summary="Sprint delivered.",
            ),
        )
        return mark_node_completed(
            worker_state,
            node_name="team_lead",
            stage="team_lead",
            status="team_lead_sprint_handoff_ready",
        )

    result = HeadAgent(
        workers=HeadWorkers(
            business_analyst=business_analyst,
            architect=architect,
            project_manager=project_manager,
            team_lead=team_lead,
        ),
        executor=SequencingHeadExecutor(),
    ).run(state)

    messages = AgentMessageStore(run_dir).read()
    assert result["status"] == "head_delivery_completed"
    assert result["completed_nodes"] == [
        "business_analyst",
        "architecture",
        "project_management",
        "team_lead",
        "head",
    ]
    assert result["feature_queue"][0]["id"] == "F1"
    assert result["active_feature_id"] == "F1"
    assert result["work_board"]["items"][0]["item_id"] == "F1"
    assert (run_dir / "head" / "planning-history.json").exists()
    assert (run_dir / "head" / "result.json").exists()
    assert [message.intent for message in messages] == [
        "request_business_analysis",
        "agent_response",
        "request_architecture",
        "agent_response",
        "request_project_management",
        "agent_response",
        "request_sprint_delivery",
        "agent_response",
    ]
    assert messages[0].from_agent == "head-agent"
    assert messages[0].to_agent == "business-analyst-agent"
    assert messages[2].from_agent == "head-agent"
    assert messages[2].to_agent == "architect-agent"
    assert messages[4].from_agent == "head-agent"
    assert messages[4].to_agent == "project-manager-agent"
    assert messages[6].from_agent == "head-agent"
    assert messages[6].to_agent == "team-lead-agent"
    assert messages[7].to_agent == "head-agent"


def test_head_board_is_visible_before_pm_feature_queue(tmp_path):
    state = initial_delivery_state(run_id="head-board", run_dir=tmp_path)
    seen: dict[str, object] = {}

    def business_analyst(worker_state: DeliveryState) -> DeliveryState:
        saved = json.loads((tmp_path / ".delivery-state.json").read_text(encoding="utf-8"))
        seen["items"] = [item["item_id"] for item in saved["work_board"]["items"]]
        seen["status"] = saved["work_board"]["items"][0]["status"]
        seen["assigned_agent"] = saved["work_board"]["items"][0]["assigned_agent"]
        return mark_node_completed(
            worker_state,
            node_name="business_analyst",
            stage="business_analysis",
            status="business_analysis_completed",
        )

    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=business_analyst,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_business_analyst(reason="Analyze.", message="Analyze requirements.")

    assert seen == {
        "items": ["BA", "ARCH", "PM", "TL"],
        "status": "in_progress",
        "assigned_agent": "business-analyst-agent",
    }


def test_head_codex_review_does_not_send_message_to_unknown_target(tmp_path):
    state = initial_delivery_state(run_id="review-test", run_dir=tmp_path)
    reviewer = FakeCodexReviewer("BA is ready for architecture.")
    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=4,
        codex_reviewer=reviewer,
    )

    response = toolbox.codex_review(
        target_agent="planning-architect-review",
        purpose="Review BA artifacts.",
        question="Is BA ready for architecture?",
        artifact_refs="upstream-planning/business-analysis.md",
        intent="analysis_only",
        target="planning-architect-review",
    )

    messages = AgentMessageStore(tmp_path).read()
    assert messages == []
    assert reviewer.requests[0].target_agent == "planning-architect-review"
    assert "not_sent_no_known_target_agent" in response
    assert "advisory_only" in response
    assert "can_block_delivery" in response


def test_head_agent_executor_does_not_expose_block_planning_tool(tmp_path):
    state = initial_delivery_state(run_id="no-block-tool", run_dir=tmp_path)
    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=4,
    )

    tool_names = {
        tool.__name__
        for tool in __import__(
            "agentic_company.agents.head.executor",
            fromlist=["langchain_tools"],
        ).langchain_tools(toolbox)
    }

    assert "block_planning" not in tool_names
    assert {
        "run_business_analyst",
        "run_architect",
        "run_project_manager",
        "run_team_lead",
        "codex_review",
        "inspect_delivery_status",
        "complete_delivery",
    } <= tool_names


def test_head_codex_review_marks_explicit_board_item_as_review(tmp_path):
    state = initial_delivery_state(run_id="review-board", run_dir=tmp_path)
    state["work_items"] = [
        {
            "id": "BA",
            "title": "Business analysis",
            "delivery_order": 1,
            "sprint_id": "planning",
            "suggested_owner_agent": "business-analyst-agent",
        }
    ]
    state["active_feature_id"] = "BA"
    reviewer = FakeCodexReviewer("BA is ready.")
    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=4,
        codex_reviewer=reviewer,
    )

    toolbox.codex_review(target="BA", reason="Review BA.", message="Review BA artifacts.")

    saved = json.loads((tmp_path / ".delivery-state.json").read_text(encoding="utf-8"))
    assert saved["work_board"]["items"][0]["status"] == "review"
    assert saved["work_board"]["items"][0]["lane"] == "review"


def test_head_codex_review_does_not_mark_active_feature_without_explicit_target(tmp_path):
    state = initial_delivery_state(run_id="review-active-feature", run_dir=tmp_path)
    state["feature_queue"] = [
        {
            "id": "F1",
            "title": "Create tasks",
            "delivery_order": 1,
            "sprint_id": "sprint-01",
            "suggested_owner_agent": "fullstack-agent",
        }
    ]
    state["active_feature_id"] = "F1"
    reviewer = FakeCodexReviewer("Status inspection is readable.")
    toolbox = HeadToolbox(
        delivery_state=state,
        workers=HeadWorkers(
            business_analyst=lambda state: state,
            architect=lambda state: state,
            project_manager=lambda state: state,
            team_lead=lambda state: state,
        ),
        max_steps=4,
        codex_reviewer=reviewer,
    )

    toolbox.codex_review(
        target_agent="codex-status-inspector",
        reason="Review status inspection.",
        message="Review status inspection.",
    )

    assert toolbox.delivery_state.get("feature_statuses", {}) == {}
    saved = json.loads((tmp_path / ".delivery-state.json").read_text(encoding="utf-8"))
    assert saved.get("feature_statuses", {}) == {}


def test_head_agent_default_step_budget_allows_complex_runs(tmp_path):
    class CapturingExecutor:
        def __init__(self) -> None:
            self.max_steps = 0

        def run(
            self,
            *,
            delivery_state: DeliveryState,
            workers: HeadWorkers,
            max_steps: int,
        ) -> HeadExecutorResult:
            self.max_steps = max_steps
            return HeadExecutorResult(delivery_state, [])

    executor = CapturingExecutor()
    state = initial_delivery_state(run_id="head-budget", run_dir=tmp_path)

    HeadAgent(executor=executor).run(state)

    assert executor.max_steps == 100
