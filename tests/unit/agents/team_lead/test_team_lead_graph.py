import json
from collections.abc import Sequence

from agentic_company.agents.team_lead.executor import (
    TEAM_LEAD_SYSTEM_PROMPT,
    build_team_lead_executor_prompt,
)
from agentic_company.agents.team_lead.graph import (
    TEAM_LEAD_AGENT_GRAPH_NODE_ORDER,
    render_team_lead_agent_graph_mermaid,
    run_team_lead_agent_graph,
)
from agentic_company.agents.team_lead.tools import (
    CodexReviewerLike,
    TeamLeadExecutorResult,
    TeamLeadToolbox,
    TeamLeadWorkers,
)
from agentic_company.platform.codex_review import (
    CodexReviewRequest,
    CodexReviewResult,
)
from agentic_company.platform.messages import AgentMessageStore
from agentic_company.platform.run_trace import load_tool_call_events
from agentic_company.platform.state import (
    DELIVERY_STATE_SNAPSHOT,
    DeliveryState,
    initial_delivery_state,
)
from agentic_company.platform.status_inspector import (
    StatusInspectionRequest,
    StatusInspectionResult,
    StatusInspectorLike,
)


class ScriptedExecutor:
    def __init__(
        self,
        calls: Sequence[tuple[str, str | None, str]],
        *,
        codex_reviewer: CodexReviewerLike | None = None,
        status_inspector: StatusInspectorLike | None = None,
    ) -> None:
        self.calls = list(calls)
        self.codex_reviewer = codex_reviewer
        self.status_inspector = status_inspector or FakeStatusInspector()
        self.seen_sprint: dict[str, object] = {}

    def run(
        self,
        *,
        delivery_state: DeliveryState,
        sprint: dict[str, object],
        workers: TeamLeadWorkers,
        max_steps: int,
    ) -> TeamLeadExecutorResult:
        self.seen_sprint = sprint
        toolbox = TeamLeadToolbox(
            delivery_state=delivery_state,
            sprint=sprint,
            workers=workers,
            max_steps=max_steps,
            codex_reviewer=self.codex_reviewer,
            status_inspector=self.status_inspector,
        )
        for tool, target, reason in self.calls:
            if tool == "run_handoff":
                getattr(toolbox, tool)(
                    handoff_scope="sprint_handoff",
                    sprint_id=target or "",
                    reason=reason,
                    message=reason,
                )
            else:
                getattr(toolbox, tool)(target=target, reason=reason, message=reason)
        return toolbox.result()


class FakeCodexReviewer(CodexReviewerLike):
    def __init__(self, content: str = "Review feedback.") -> None:
        self.content = content
        self.requests: list[CodexReviewRequest] = []

    def run(self, request: CodexReviewRequest) -> CodexReviewResult:
        self.requests.append(request)
        return CodexReviewResult(
            status="reviewed",
            content=self.content,
            artifact_refs=request.artifact_refs,
            summary_artifact="team-lead/codex-review/summary.md",
            prompt_artifact="team-lead/codex-review/prompt.md",
            log_artifact="team-lead/codex-review/execution.log",
        )


class FakeStatusInspector:
    def __init__(self, *, can_complete_sprint: bool = True) -> None:
        self.can_complete_sprint = can_complete_sprint
        self.requests: list[StatusInspectionRequest] = []

    def run(self, request: StatusInspectionRequest) -> StatusInspectionResult:
        self.requests.append(request)
        payload = {
            "status": "inspected",
            "scope": "sprint",
            "sprint_id": request.correlation_id or "sprint-01",
            "sprint_status": "ready_to_complete" if self.can_complete_sprint else "running",
            "tasks": [],
            "workers_called": [],
            "gates": {
                "implementation_done": self.can_complete_sprint,
                "qa_passed": self.can_complete_sprint,
                "deployment_done": False,
                "handoff_ready": self.can_complete_sprint,
            },
            "can_complete_sprint": self.can_complete_sprint,
            "status_summary": "Sprint inspected.",
            "status_legend": {"ready_to_complete": "Handoff evidence is accepted."},
        }
        return StatusInspectionResult(
            status="inspected",
            payload=payload,
            artifact_refs=[],
            result_artifact="team-lead/status-inspections/status.json",
            summary_artifact="team-lead/status-inspections/summary.md",
            prompt_artifact="team-lead/status-inspections/prompt.md",
            log_artifact="team-lead/status-inspections/execution.log",
            execution_id=request.execution_id,
            codex_thread_id="thread-status",
        )


def test_team_lead_graph_exposes_agent_executor_node():
    mermaid = render_team_lead_agent_graph_mermaid()

    assert TEAM_LEAD_AGENT_GRAPH_NODE_ORDER == (
        "prepare_sprint",
        "run_agent_executor",
        "apply_result",
    )
    assert "run_agent_executor" in mermaid


def test_team_lead_toolbox_preserves_existing_sprint_history(tmp_path):
    state = initial_delivery_state(run_id="history-test", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Feature one", "sprint_id": "sprint-01"}]
    history_path = tmp_path / "team-lead" / "sprint-01-history.json"
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "step": 1,
                        "tool": "inspect_sprint_status",
                        "target": "sprint-01",
                        "reason": "Initial readback.",
                        "result_status": "running",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fullstack_worker(delivery_state):
        updated = {**delivery_state}
        updated["status"] = "fullstack_feature_implemented"
        updated["stage"] = "fullstack"
        return updated

    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=fullstack_worker,
            qa=lambda delivery_state: delivery_state,
            deployment=lambda delivery_state: delivery_state,
            handoff=lambda delivery_state: delivery_state,
        ),
        max_steps=10,
    )

    toolbox.run_fullstack(target="F1", reason="Continue sprint.", message="Continue sprint.")
    result = toolbox.result()

    assert [step["tool"] for step in result.history] == [
        "inspect_sprint_status",
        "run_fullstack",
    ]
    assert [step["step"] for step in result.history] == [1, 2]
    persisted = json.loads(history_path.read_text(encoding="utf-8"))
    assert [step["tool"] for step in persisted["steps"]] == [
        "inspect_sprint_status",
        "run_fullstack",
    ]


def test_team_lead_prompt_uses_lightweight_handoff_sanity_review(tmp_path):
    state = initial_delivery_state(run_id="prompt-test", run_dir=tmp_path)
    state["artifacts"] = [
        {
            "kind": "planning",
            "owner_agent": "project-manager-agent",
            "path": "upstream-planning/project-management/release-plan.md",
            "visibility": "user",
        }
    ]
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]

    prompt = build_team_lead_executor_prompt(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
    )

    assert "upstream_planning_context" in prompt
    assert "upstream-planning/project-management/release-plan.md" in prompt
    assert "If the artifact set is large, unclear, or potentially conflicting" in prompt
    assert "lightweight coordinator sanity" in prompt
    assert "Do not redo specialist work or QA" in prompt
    assert "coordinator_quality_review_policy" in prompt
    assert "keep coordinator review lightweight" in prompt
    assert "Use `codex_review` only when the response is unclear" in prompt
    assert "coordinator_recovery_policy" in prompt
    assert "run_fullstack" in prompt
    assert "run_qa" in prompt
    assert "run_deployment" in prompt
    assert "Treat Azure/dev deployment as a supported platform path" in prompt
    assert "run_handoff" in prompt
    assert "status/evidence readback" in prompt
    assert "Do not let the inspector choose the next worker" in prompt
    assert "routing remains your responsibility" in prompt
    assert "Treat Deployment and post-deploy QA failures as routing signals" in prompt
    assert (
        "Application\n  runtime/cloud-readiness mismatches go to Fullstack"
        in TEAM_LEAD_SYSTEM_PROMPT
    )
    assert "Azure resources, registry, secrets, ingress, rollout" in TEAM_LEAD_SYSTEM_PROMPT
    assert "Never end the sprint with block_sprint before requesting" in prompt
    assert "blocked/partial sprint" in TEAM_LEAD_SYSTEM_PROMPT
    assert "failed, blocked, waiting, refused, precondition" in prompt
    assert "rerun the owning downstream tool" in prompt
    assert "call `codex_review` before rerunning the owner" in prompt
    assert "5 repair attempt" in prompt
    assert "Do not ask Codex Review to perform QA" in prompt
    assert "review upstream planning artifacts" in TEAM_LEAD_SYSTEM_PROMPT
    assert "treat Azure/dev deployment as supported" in TEAM_LEAD_SYSTEM_PROMPT
    assert "deployed working product URL" in TEAM_LEAD_SYSTEM_PROMPT
    assert "release_gates" in TEAM_LEAD_SYSTEM_PROMPT
    assert "call Deployment Agent before final handoff" in TEAM_LEAD_SYSTEM_PROMPT
    assert "silently skipping deployment" in TEAM_LEAD_SYSTEM_PROMPT
    assert "artifact refs are missing" in TEAM_LEAD_SYSTEM_PROMPT
    assert (
        "returned artifact_refs as the sprint evidence passed upstream" in TEAM_LEAD_SYSTEM_PROMPT
    )
    assert "status-only\nevidence, not routing advice" in TEAM_LEAD_SYSTEM_PROMPT


def test_team_lead_status_context_keeps_pending_feature_pending_before_fullstack(tmp_path):
    captured: dict[str, object] = {}

    class CapturingStatusInspector(FakeStatusInspector):
        def run(self, request: StatusInspectionRequest) -> StatusInspectionResult:
            captured["context"] = dict(request.status_context)
            return super().run(request)

    state = initial_delivery_state(run_id="pending-status", run_dir=tmp_path)
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
    state["feature_statuses"] = {"F1": "review"}
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
        status_inspector=CapturingStatusInspector(can_complete_sprint=False),
    )

    toolbox.inspect_sprint_status(target="sprint-01", reason="Inspect pending feature.")

    context = captured["context"]
    assert isinstance(context, dict)
    assert context["tasks"][0]["status"] == "pending"
    assert context["tasks"][0]["owner_agent"] == "fullstack-agent"
    assert context["tasks"][0]["evidence_refs"] == []


def test_team_lead_run_qa_tool_event_keeps_original_target_after_active_advances(tmp_path):
    def qa(state: DeliveryState) -> DeliveryState:
        statuses = dict(state.get("feature_statuses", {}))
        statuses["US-rooms"] = "qa_passed"
        return {
            **state,
            "stage": "quality",
            "status": "qa_feature_passed_next_feature_ready",
            "qa_status": "passed",
            "active_feature_id": "US-contacts-friends",
            "completed_feature_ids": ["US-rooms"],
            "feature_statuses": statuses,
        }

    state = initial_delivery_state(run_id="qa-target", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "US-rooms", "title": "Rooms", "sprint_id": "sprint-02", "delivery_order": 1},
        {
            "id": "US-contacts-friends",
            "title": "Contacts",
            "sprint_id": "sprint-02",
            "delivery_order": 2,
        },
    ]
    state["team_lead_sprint_id"] = "sprint-02"
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-02"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=qa,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_qa(target="US-rooms", reason="Validate rooms.")

    events = load_tool_call_events(tmp_path)
    qa_event = [event for event in events if event.tool_name == "run_qa"][-1]
    assert qa_event.work_item_id == "US-rooms"
    assert qa_event.input_summary["work_item_id"] == "US-rooms"
    assert toolbox.delivery_state["active_feature_id"] == "US-contacts-friends"


def test_team_lead_blocks_final_project_report_for_non_final_sprint(tmp_path):
    called = False
    plan_dir = tmp_path / "upstream-planning" / "project-management"
    plan_dir.mkdir(parents=True)
    (plan_dir / "release-plan.json").write_text(
        '{"sprints":[{"sprint_id":"sprint-01","is_final":false},'
        '{"sprint_id":"sprint-02","is_final":true}]}',
        encoding="utf-8",
    )

    def handoff(state: DeliveryState) -> DeliveryState:
        nonlocal called
        called = True
        return state

    state = initial_delivery_state(run_id="handoff-gate", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Feature one", "sprint_id": "sprint-01", "delivery_order": 1},
        {"id": "F2", "title": "Feature two", "sprint_id": "sprint-02", "delivery_order": 2},
    ]
    state["team_lead_sprint_id"] = "sprint-01"
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=handoff,
        ),
        max_steps=6,
    )

    response = toolbox.run_handoff("final_project_report", reason="Create final report.")

    assert called is False
    assert "team_lead_final_handoff_not_ready" in response


def test_team_lead_agent_executor_calls_tools_selected_by_executor(tmp_path):
    visited: list[str] = []
    feature_queue = [
        {"id": "F1", "title": "Create tasks", "delivery_order": 1},
        {"id": "F2", "title": "Mark done", "delivery_order": 2},
    ]
    executor = ScriptedExecutor(
        [
            ("run_fullstack", "F1", "Implement F1."),
            ("run_qa", "F1", "Validate F1."),
            ("run_fullstack", "F2", "Implement F2."),
            ("run_qa", "F2", "Validate F2."),
            ("run_deployment", "sprint-01", "Deploy sprint."),
            ("run_post_deploy_qa", "post-deploy", "Validate deployment."),
            ("run_handoff", "sprint-01", "Create sprint handoff."),
            ("codex_review", "sprint-01", "Review handoff business alignment."),
            ("inspect_sprint_status", "sprint-01", "Confirm sprint can complete."),
            ("complete_sprint", "sprint-01", "Sprint done."),
        ],
        codex_reviewer=FakeCodexReviewer("Handoff is business aligned."),
    )

    def fullstack(state: DeliveryState) -> DeliveryState:
        feature_id = str(state["active_feature_id"])
        visited.append(f"fullstack:{feature_id}")
        statuses = dict(state.get("feature_statuses", {}))
        statuses[feature_id] = "implemented"
        return {
            **state,
            "stage": "fullstack",
            "status": "fullstack_feature_implemented",
            "feature_statuses": statuses,
            "completed_nodes": [*state["completed_nodes"], "fullstack"],
        }

    def qa(state: DeliveryState) -> DeliveryState:
        feature_id = str(state["active_feature_id"])
        visited.append(f"qa:{feature_id}")
        statuses = dict(state.get("feature_statuses", {}))
        statuses[feature_id] = "qa_passed"
        completed = list(state.get("completed_feature_ids", []))
        if feature_id not in completed:
            completed.append(feature_id)
        return {
            **state,
            "stage": "qa",
            "status": "qa_passed",
            "active_feature_id": None if feature_id != "post-deploy" else "post-deploy",
            "qa_status": "passed",
            "feature_statuses": statuses,
            "completed_feature_ids": completed,
            "completed_nodes": [*state["completed_nodes"], f"qa:{feature_id}"],
        }

    def deployment(state: DeliveryState) -> DeliveryState:
        visited.append("deployment")
        return {
            **state,
            "stage": "deployment",
            "status": "deployment_deployed",
            "deployment_status": "deployed",
            "public_url": "https://web.example.com",
            "public_urls": ["https://web.example.com"],
            "completed_nodes": [*state["completed_nodes"], "deployment"],
        }

    def handoff(state: DeliveryState) -> DeliveryState:
        visited.append("handoff")
        return {
            **state,
            "stage": "handoff",
            "status": "handoff_ready",
            "artifacts": [
                *state.get("artifacts", []),
                {
                    "path": "handoff/sprints/sprint-01/release-report.html",
                    "kind": "handoff",
                    "owner_agent": "documentation-handoff-agent",
                    "visibility": "user",
                },
            ],
            "completed_nodes": [*state["completed_nodes"], "handoff"],
        }

    state = initial_delivery_state(run_id="team-lead-test", run_dir=tmp_path)
    state["feature_queue"] = feature_queue

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=qa,
            deployment=deployment,
            handoff=handoff,
        ),
        executor=executor,
    )

    assert visited == [
        "fullstack:F1",
        "qa:F1",
        "fullstack:F2",
        "qa:F2",
        "deployment",
        "qa:post-deploy",
        "handoff",
    ]
    assert result["completed_feature_ids"] == ["F1", "F2"]
    assert result["feature_statuses"] == {"F1": "qa_passed", "F2": "qa_passed"}
    assert result["post_deploy_qa_status"] == "passed"
    assert result["status"] == "team_lead_sprint_handoff_ready"

    team_lead_result = json.loads((tmp_path / "team-lead" / "sprint-01-result.json").read_text())
    assert team_lead_result["status"] == "handoff_ready"
    assert team_lead_result["handoff_status"] == "ready"
    assert team_lead_result["completed_features"] == ["F1", "F2"]
    assert "handoff/sprints/sprint-01/release-report.html" in team_lead_result["artifact_refs"]
    history = json.loads((tmp_path / "team-lead" / "sprint-01-history.json").read_text())
    assert [step["tool"] for step in history["steps"]][-3:] == [
        "codex_review",
        "inspect_sprint_status",
        "complete_sprint",
    ]
    assert "codex_review" in [step["tool"] for step in history["steps"]]
    assert "inspect_sprint_status" in [step["tool"] for step in history["steps"]]
    handoff_messages = AgentMessageStore(tmp_path).read(
        to_agent="documentation-handoff-agent",
        from_agent="team-lead-agent",
        intent="request_handoff",
    )
    assert len(handoff_messages) == 1
    assert handoff_messages[0].content == "Create sprint handoff."
    assert executor.seen_sprint["sprint_id"] == "sprint-01"


def test_team_lead_codex_review_sends_feedback_message_to_target_agent(tmp_path):
    reviewer = FakeCodexReviewer("Revise the report for business alignment.")
    toolbox = TeamLeadToolbox(
        delivery_state=initial_delivery_state(run_id="review-test", run_dir=tmp_path),
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=4,
        codex_reviewer=reviewer,
    )

    response = json.loads(
        toolbox.codex_review(
            target_agent="documentation-handoff-agent",
            purpose="Review the release report.",
            question="Is the report business aligned?",
            artifact_refs="handoff/release-report.html, 00-requirements.md",
            intent="review_feedback",
            target="sprint-01",
            reason="Need report review.",
        )
    )

    messages = AgentMessageStore(tmp_path).read(
        from_agent="team-lead-agent",
        to_agent="documentation-handoff-agent",
        intent="review_feedback",
    )
    assert reviewer.requests[0].artifact_refs == [
        "handoff/release-report.html",
        "00-requirements.md",
    ]
    assert len(messages) == 1
    assert messages[0].content == "Revise the report for business alignment."
    assert response["downstream_response"]["content"] == messages[0].content


def test_complete_sprint_accepts_handoff_artifacts_without_code_enforced_codex_review(tmp_path):
    def handoff(state: DeliveryState) -> DeliveryState:
        return {
            **state,
            "stage": "handoff",
            "status": "handoff_ready",
            "artifacts": [
                *state.get("artifacts", []),
                {
                    "path": "handoff/sprints/sprint-01/release-report.html",
                    "kind": "handoff",
                    "owner_agent": "documentation-handoff-agent",
                    "visibility": "user",
                },
            ],
        }

    state = initial_delivery_state(run_id="handoff-review-required", run_dir=tmp_path)
    state["post_deploy_qa_status"] = "passed"
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=handoff,
        ),
        max_steps=8,
        status_inspector=FakeStatusInspector(),
    )

    toolbox.run_handoff(
        handoff_scope="sprint_handoff",
        sprint_id="sprint-01",
        reason="Create handoff.",
        message="Create handoff.",
    )
    toolbox.inspect_sprint_status(
        target="sprint-01",
        reason="Confirm handoff evidence before completion.",
        message="Confirm handoff evidence before completion.",
    )
    response = json.loads(
        toolbox.complete_sprint(
            target="sprint-01",
            reason="Complete sprint.",
            message="Complete sprint.",
        )
    )

    assert response["status"] == "team_lead_sprint_handoff_ready"
    assert "Sprint completed" in response["message"]
    assert toolbox.history[-1]["result_status"] == "team_lead_sprint_handoff_ready"


def test_team_lead_accepts_second_handoff_version_after_one_codex_review(tmp_path):
    handoff_calls: list[str] = []

    def handoff(state: DeliveryState) -> DeliveryState:
        handoff_calls.append(str(state.get("agent_call_message_id") or ""))
        return {
            **state,
            "stage": "handoff",
            "status": "handoff_ready",
            "artifacts": [
                *state.get("artifacts", []),
                {
                    "path": f"handoff/sprints/sprint-01/release-{len(handoff_calls)}.html",
                    "kind": "handoff",
                    "owner_agent": "documentation-handoff-agent",
                    "visibility": "user",
                },
            ],
        }

    state = initial_delivery_state(run_id="handoff-review-rerun", run_dir=tmp_path)
    state["post_deploy_qa_status"] = "passed"
    reviewer = FakeCodexReviewer("Improve the report language for client business alignment.")
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=handoff,
        ),
        max_steps=8,
        codex_reviewer=reviewer,
        status_inspector=FakeStatusInspector(),
    )

    toolbox.run_handoff(
        handoff_scope="sprint_handoff",
        sprint_id="sprint-01",
        reason="Create handoff.",
        message="Create handoff.",
    )
    toolbox.codex_review(
        target_agent="documentation-handoff-agent",
        purpose="Review handoff report.",
        question="Is the handoff HTML business aligned?",
        artifact_refs="handoff/release-report.html",
        target="sprint-01",
        reason="Review handoff.",
    )
    toolbox.run_handoff(
        handoff_scope="sprint_handoff",
        sprint_id="sprint-01",
        reason="Revise handoff from review.",
        message=reviewer.content,
    )
    toolbox.inspect_sprint_status(
        target="sprint-01",
        reason="Confirm revised handoff before completion.",
        message="Confirm revised handoff before completion.",
    )
    response = json.loads(
        toolbox.complete_sprint(
            target="sprint-01",
            reason="Accept second handoff and complete sprint.",
            message="Accept second handoff and complete sprint.",
        )
    )

    assert len(handoff_calls) == 2
    assert response["status"] == "team_lead_sprint_handoff_ready"
    assert toolbox.history[-1]["result_status"] == "team_lead_sprint_handoff_ready"


def test_team_lead_can_route_failed_qa_back_to_fullstack_by_tool_call(tmp_path):
    visited: list[str] = []
    executor = ScriptedExecutor(
        [
            ("run_fullstack", "F1", "Implement F1."),
            ("run_qa", "F1", "Validate F1."),
            ("run_fullstack", "F1", "Repair F1."),
            ("run_qa", "F1", "Revalidate F1."),
            ("run_deployment", "sprint-01", "Deploy sprint."),
            ("run_post_deploy_qa", "post-deploy", "Validate deployment."),
            ("run_handoff", "sprint-01", "Create handoff."),
            ("codex_review", "sprint-01", "Review handoff."),
            ("inspect_sprint_status", "sprint-01", "Confirm sprint can complete."),
            ("complete_sprint", "sprint-01", "Sprint done."),
        ],
        codex_reviewer=FakeCodexReviewer("Handoff is acceptable."),
    )
    qa_attempts = 0

    def fullstack(state: DeliveryState) -> DeliveryState:
        feature_id = str(state["active_feature_id"])
        visited.append(f"fullstack:{feature_id}")
        return {**state, "stage": "fullstack", "status": "fullstack_feature_implemented"}

    def qa(state: DeliveryState) -> DeliveryState:
        nonlocal qa_attempts
        feature_id = str(state["active_feature_id"])
        visited.append(f"qa:{feature_id}")
        if feature_id == "post-deploy" or qa_attempts:
            completed = [*state.get("completed_feature_ids", []), feature_id]
            return {
                **state,
                "stage": "qa",
                "status": "qa_passed",
                "qa_status": "passed",
                "completed_feature_ids": completed,
                "feature_statuses": {**state.get("feature_statuses", {}), feature_id: "qa_passed"},
                "active_feature_id": None if feature_id != "post-deploy" else "post-deploy",
            }
        qa_attempts += 1
        return {
            **state,
            "stage": "qa",
            "status": "qa_feature_failed_repair_ready",
            "qa_status": "failed",
            "active_feature_id": feature_id,
            "feature_statuses": {**state.get("feature_statuses", {}), feature_id: "qa_failed"},
        }

    def deployment(state: DeliveryState) -> DeliveryState:
        visited.append("deployment")
        return {**state, "deployment_status": "deployed", "status": "deployment_deployed"}

    def handoff(state: DeliveryState) -> DeliveryState:
        visited.append("handoff")
        return {
            **state,
            "stage": "handoff",
            "status": "handoff_ready",
            "artifacts": [
                *state.get("artifacts", []),
                {
                    "path": "handoff/sprints/sprint-01/release-report.html",
                    "kind": "handoff",
                    "owner_agent": "documentation-handoff-agent",
                    "visibility": "user",
                },
            ],
        }

    state = initial_delivery_state(run_id="repair-test", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=qa,
            deployment=deployment,
            handoff=handoff,
        ),
        executor=executor,
    )

    assert visited[:4] == ["fullstack:F1", "qa:F1", "fullstack:F1", "qa:F1"]
    assert result["completed_feature_ids"] == ["F1"]
    assert result["status"] == "team_lead_sprint_handoff_ready"


def test_team_lead_toolbox_allows_tl_to_choose_deployment_timing(tmp_path):
    visited: list[str] = []
    executor = ScriptedExecutor(
        [
            ("run_fullstack", "F1", "Implement F1."),
            ("run_qa", "F1", "Validate F1."),
            ("run_deployment", "sprint-01", "Try deployment too early."),
            ("run_fullstack", "F2", "Implement F2."),
            ("run_qa", "F2", "Validate F2."),
            ("run_deployment", "sprint-01", "Deploy sprint."),
        ]
    )

    def fullstack(state: DeliveryState) -> DeliveryState:
        visited.append(f"fullstack:{state['active_feature_id']}")
        return {**state, "stage": "fullstack", "status": "fullstack_feature_implemented"}

    def qa(state: DeliveryState) -> DeliveryState:
        feature_id = str(state["active_feature_id"])
        visited.append(f"qa:{feature_id}")
        completed = [*state.get("completed_feature_ids", []), feature_id]
        return {
            **state,
            "stage": "qa",
            "status": "qa_passed",
            "qa_status": "passed",
            "completed_feature_ids": completed,
            "feature_statuses": {**state.get("feature_statuses", {}), feature_id: "qa_passed"},
        }

    def deployment(state: DeliveryState) -> DeliveryState:
        visited.append("deployment")
        return {
            **state,
            "stage": "deployment",
            "status": "deployment_deployed",
            "deployment_status": "deployed",
        }

    state = initial_delivery_state(run_id="deployment-guard", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Create tasks", "delivery_order": 1},
        {"id": "F2", "title": "Mark done", "delivery_order": 2},
    ]

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=qa,
            deployment=deployment,
            handoff=lambda state: state,
        ),
        executor=executor,
    )

    assert visited == [
        "fullstack:F1",
        "qa:F1",
        "deployment",
        "fullstack:F2",
        "qa:F2",
        "deployment",
    ]
    history = json.loads((tmp_path / "team-lead" / "sprint-01-history.json").read_text())
    assert history["steps"][2]["result_status"] == "deployment_deployed"
    assert result["deployment_status"] == "deployed"


def test_work_board_keeps_future_sprint_items_visible(tmp_path):
    state = initial_delivery_state(run_id="board-sprints", run_dir=tmp_path)
    state["team_lead_sprint_id"] = "sprint-01"
    state["feature_queue"] = [
        {"id": "F1", "title": "Current", "delivery_order": 1, "sprint_id": "sprint-01"},
        {"id": "F2", "title": "Future", "delivery_order": 1, "sprint_id": "sprint-02"},
    ]
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_fullstack(target="F1", reason="Start.", message="Start F1.")

    board = toolbox.delivery_state["work_board"]
    assert [item["item_id"] for item in board["items"]] == ["F1", "F2"]
    assert board["items"][0]["status"] == "in_progress"
    assert board["items"][1]["sprint_id"] == "sprint-02"
    assert board["items"][1]["status"] == "pending"


def test_team_lead_can_delegate_qa_when_it_decides_review_is_needed(tmp_path):
    state = initial_delivery_state(run_id="qa-precondition", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    state["active_feature_id"] = "F1"
    visited: list[str] = []
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: visited.append("qa") or state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    response = json.loads(toolbox.run_qa(target="F1", reason="Too early.", message="Validate."))

    assert visited == ["qa"]
    assert response["status"] == "initialized"
    assert "run_qa completed" in response["message"]
    assert toolbox.history[-1]["result_status"] == "initialized"
    board = toolbox.delivery_state["work_board"]
    assert board["items"][0]["item_id"] == "F1"
    assert board["items"][0]["status"] == "in_qa"
    assert board["items"][0]["lane"] == "qa"


def test_team_lead_sends_canonical_work_item_packet_to_specialists(tmp_path):
    state = initial_delivery_state(run_id="canonical-work-item", run_dir=tmp_path)
    state["feature_queue"] = [
        {
            "id": "F1",
            "title": "Create tasks",
            "description": "Create tasks through API and UI.",
            "delivery_order": 1,
            "sprint_id": "sprint-01",
            "acceptance_criteria": ["API rejects invalid titles with a clear error."],
            "qa_notes": ["Verify invalid-title behavior."],
            "source_refs": ["F1", "AC-F1"],
            "suggested_owner_agent": "fullstack-agent",
        }
    ]
    state["active_feature_id"] = "F1"
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_qa(
        target="F1",
        reason="Validate.",
        message="Validate F1 and require HTTP 400.",
    )

    messages = AgentMessageStore(tmp_path).read(to_agent="qa-agent", intent="request_qa")
    assert len(messages) == 1
    content = messages[0].content
    assert "Canonical work item packet" in content
    assert '"id": "F1"' in content
    assert "API rejects invalid titles with a clear error." in content
    assert "Contract precedence" in content
    assert "Do not add stricter acceptance criteria" in content
    assert "coordinator note appears to conflict" in content


def test_team_lead_checkpoints_in_qa_before_long_qa_worker_runs(tmp_path):
    state = initial_delivery_state(run_id="qa-live-state", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    state["active_feature_id"] = "F1"
    seen: dict[str, object] = {}

    def qa(worker_state: DeliveryState) -> DeliveryState:
        saved = json.loads((tmp_path / DELIVERY_STATE_SNAPSHOT).read_text(encoding="utf-8"))
        seen["status"] = saved["work_board"]["items"][0]["status"]
        seen["lane"] = saved["work_board"]["items"][0]["lane"]
        seen["assigned_agent"] = saved["work_board"]["items"][0]["assigned_agent"]
        return worker_state

    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=qa,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_qa(target="F1", reason="Validate.", message="Validate F1.")

    assert seen == {
        "status": "in_qa",
        "lane": "qa",
        "assigned_agent": "qa-agent",
    }


def test_work_board_marks_implemented_items_as_review_lane(tmp_path):
    state = initial_delivery_state(run_id="implemented-lane", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]

    def fullstack(worker_state: DeliveryState) -> DeliveryState:
        return {**worker_state, "stage": "fullstack", "status": "fullstack_feature_implemented"}

    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_fullstack(target="F1", reason="Implement.", message="Implement F1.")

    item = toolbox.delivery_state["work_board"]["items"][0]
    assert item["status"] == "implemented"
    assert item["lane"] == "review"


def test_work_board_can_move_from_review_back_to_doing(tmp_path):
    state = initial_delivery_state(run_id="review-to-doing", run_dir=tmp_path)
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    state["active_feature_id"] = "F1"
    state["feature_statuses"] = {"F1": "review"}
    visited: list[str] = []

    def fullstack(worker_state: DeliveryState) -> DeliveryState:
        saved = json.loads((tmp_path / DELIVERY_STATE_SNAPSHOT).read_text(encoding="utf-8"))
        visited.append(saved["work_board"]["items"][0]["status"])
        return {**worker_state, "stage": "fullstack", "status": "fullstack_feature_implemented"}

    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        max_steps=6,
    )

    toolbox.run_fullstack(target="F1", reason="Repair from review.", message="Repair F1.")

    assert visited == ["in_progress"]
    item = toolbox.delivery_state["work_board"]["items"][0]
    assert item["status"] == "implemented"
    assert item["lane"] == "review"


def test_team_lead_tools_do_not_block_deployment_or_handoff_by_policy(tmp_path):
    visited: list[str] = []

    def handoff(state: DeliveryState) -> DeliveryState:
        visited.append("handoff")
        return {**state, "stage": "handoff", "status": "handoff_ready"}

    state = initial_delivery_state(run_id="local-sprint", run_dir=tmp_path)
    state["team_lead_sprint_id"] = "sprint-01"
    state["feature_queue"] = [
        {"id": "F1", "title": "Foundation", "delivery_order": 1, "sprint_id": "sprint-01"}
    ]
    state["completed_feature_ids"] = ["F1"]
    state["feature_statuses"] = {"F1": "qa_passed"}
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={
            "sprint_id": "sprint-01",
            "deployment_policy": {"azure_dev": "Not allowed in this sprint."},
        },
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: visited.append("deployment") or state,
            handoff=handoff,
        ),
        max_steps=8,
    )

    deployment_response = json.loads(
        toolbox.run_deployment(target="sprint-01", reason="Try deploy.", message="Deploy.")
    )
    handoff_response = json.loads(
        toolbox.run_handoff(
            handoff_scope="sprint_handoff",
            sprint_id="sprint-01",
            reason="Handoff.",
            message="Create handoff.",
        )
    )

    assert deployment_response["status"] == "initialized"
    assert "run_deployment completed" in deployment_response["message"]
    assert visited == ["deployment", "handoff"]
    assert handoff_response["status"] == "handoff_ready"


def test_team_lead_can_retry_blocked_deployment_when_it_has_new_instructions(tmp_path):
    visited: list[str] = []
    executor = ScriptedExecutor(
        [
            ("run_deployment", "sprint-01", "Deploy sprint."),
            ("run_deployment", "sprint-01", "Retry deployment."),
        ]
    )

    def deployment(state: DeliveryState) -> DeliveryState:
        visited.append("deployment")
        return {
            **state,
            "stage": "deployment",
            "status": "deployment_blocked",
            "deployment_status": "blocked",
        }

    state = initial_delivery_state(run_id="deployment-blocked", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]
    state["completed_feature_ids"] = ["F1"]
    state["feature_statuses"] = {"F1": "qa_passed"}

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=deployment,
            handoff=lambda state: state,
        ),
        executor=executor,
    )

    assert visited == ["deployment", "deployment"]
    history = json.loads((tmp_path / "team-lead" / "sprint-01-history.json").read_text())
    assert history["steps"][1]["result_status"] == "deployment_blocked"
    assert result["deployment_status"] == "blocked"


def test_team_lead_toolbox_uses_active_feature_when_llm_target_is_noisy(tmp_path):
    visited: list[str] = []
    executor = ScriptedExecutor(
        [
            (
                "run_fullstack",
                "Fullstack Agent should implement Create and list tasks",
                "Implement.",
            ),
        ]
    )

    def fullstack(state: DeliveryState) -> DeliveryState:
        visited.append(f"fullstack:{state['active_feature_id']}")
        return {**state, "stage": "fullstack", "status": "fullstack_feature_implemented"}

    state = initial_delivery_state(run_id="noisy-target", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=fullstack,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        executor=executor,
    )

    assert visited == ["fullstack:F1"]
    assert result["blockers"] == []


def test_team_lead_default_step_budget_allows_complex_sprints(tmp_path):
    class CapturingExecutor:
        def __init__(self) -> None:
            self.max_steps = 0

        def run(
            self,
            *,
            delivery_state: DeliveryState,
            sprint: dict[str, object],
            workers: TeamLeadWorkers,
            max_steps: int,
        ) -> TeamLeadExecutorResult:
            self.max_steps = max_steps
            return TeamLeadExecutorResult(delivery_state, [])

    executor = CapturingExecutor()
    state = initial_delivery_state(run_id="team-lead-budget", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]

    run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        executor=executor,
    )

    assert executor.max_steps == 100


def test_team_lead_blocks_after_max_agent_executor_tool_calls(tmp_path):
    executor = ScriptedExecutor(
        [
            ("run_fullstack", "F1", "loop"),
            ("run_fullstack", "F1", "loop"),
            ("run_fullstack", "F1", "loop"),
        ]
    )
    state = initial_delivery_state(run_id="max-steps", run_dir=tmp_path)
    state["feature_queue"] = [{"id": "F1", "title": "Create tasks", "delivery_order": 1}]

    result = run_team_lead_agent_graph(
        state,
        workers=TeamLeadWorkers(
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        ),
        executor=executor,
        max_steps=2,
    )

    assert result["status"] == "team_lead_sprint_blocked"
    assert "exceeded max tool calls" in result["blockers"][0]
