import json
from pathlib import Path

from agentic_company.agents.head.executor import LangChainHeadExecutor
from agentic_company.agents.head.tools import HeadToolbox, HeadWorkers, write_head_result
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOL_CONTRACT_REGISTRY
from agentic_company.agents.team_lead.executor import LangChainTeamLeadExecutor
from agentic_company.agents.team_lead.graph import run_team_lead_agent_graph
from agentic_company.agents.team_lead.tools import (
    TeamLeadExecutorResult,
    TeamLeadToolbox,
    TeamLeadWorkers,
    _status_for_tool_result,
    apply_team_lead_result,
)
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.codex_review import CodexReviewResult
from agentic_company.platform.runtime_db import (
    get_work_item,
    mark_sprint_done,
    mark_sprint_started,
    materialize_planning_items,
    materialize_pm_work_items,
    next_sprint_to_run,
    record_work_item_transition,
    sprint_completion_state,
    sprint_is_final,
)
from agentic_company.platform.status_inspector import StatusInspectionResult
from agentic_company.platform.tool_contracts import ToolExecutionRecord


def test_complete_sprint_records_db_sprint_done_without_finishing_plan_04(tmp_path, monkeypatch):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.complete_sprint("PLAN-04", reason="Sprint handoff accepted."))

    assert payload["tool_name"] == "complete_sprint"
    assert len(toolbox.history or []) == 1
    assert sprint_completion_state("run", "sprint-01").status == "done"
    assert get_work_item("run", "PLAN-04").status == "in_progress"
    events = repo.list_tool_call_events(run.id, agent_id="team-lead-agent")
    assert [event.tool_name for event in events] == ["complete_sprint"]


def test_team_lead_executor_does_not_block_after_successful_complete_sprint(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    executor = LangChainTeamLeadExecutor(runtime=_CompletingRuntime())

    result = executor.run(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
    )

    assert result.delivery_state["status"] == "team_lead_sprint_handoff_ready"
    assert result.delivery_state.get("blockers", []) == []
    assert sprint_completion_state("run", "sprint-01").status == "done"


def test_complete_sprint_clears_resolved_stale_blockers(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    toolbox = TeamLeadToolbox(
        delivery_state={
            **state,
            "blockers": ["Fullstack work item US-1 did not complete successfully."],
        },
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.complete_sprint("PLAN-04", reason="Sprint handoff accepted."))

    assert payload["status"] == "team_lead_sprint_handoff_ready"
    assert toolbox.delivery_state["blockers"] == []


def test_deployment_success_moves_work_item_to_review_until_qa_passes(tmp_path, monkeypatch):
    _repo, _run, _state = _setup_runtime(tmp_path, monkeypatch)

    record_work_item_transition(
        ToolExecutionRecord(
            run_id="run",
            work_item_id="US-1",
            sprint_id="sprint-01",
            owner_agent="deployment-agent",
            tool_name="codex_exec",
            tool_call_id="run:deployment:US-1",
            attempt_id="1",
            status="deployment_deployed",
            activity_message="Deployment published US-1.",
        )
    )

    deployed_item = get_work_item("run", "US-1")
    assert deployed_item.status == "review"
    assert deployed_item.lane == "qa"
    assert deployed_item.owner_agent == "deployment-agent"
    assert deployed_item.active is True

    record_work_item_transition(
        ToolExecutionRecord(
            run_id="run",
            work_item_id="US-1",
            sprint_id="sprint-01",
            owner_agent="qa-agent",
            tool_name="run_post_deploy_qa",
            tool_call_id="run:post-deploy-qa:US-1",
            attempt_id="1",
            status="qa_passed",
            activity_message="Post-deploy QA passed US-1.",
        )
    )

    reviewed_item = get_work_item("run", "US-1")
    assert reviewed_item.status == "done"
    assert reviewed_item.lane == "done"
    assert reviewed_item.owner_agent == "qa-agent"
    assert reviewed_item.active is False


def test_team_lead_deployment_tool_result_stays_in_review_before_qa():
    assert _status_for_tool_result("run_deployment", "deployment_deployed") == "review"
    assert _status_for_tool_result("run_post_deploy_qa", "qa_passed") == "done"


def test_apply_team_lead_result_omits_resolved_stale_blockers(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    mark_sprint_done("run", "sprint-01")
    stale_state = {
        **state,
        "status": "team_lead_sprint_handoff_ready",
        "blockers": ["Fullstack work item US-1 did not complete successfully."],
    }

    apply_team_lead_result(stale_state, "sprint-01")

    result_path = tmp_path / "run" / "team-lead" / "sprint-01-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "team_lead_sprint_handoff_ready"
    assert result["blockers"] == []
    assert result["completed_work_item_ids"] == ["US-1"]


def test_team_lead_executor_blocks_if_runtime_stops_after_contract_error(tmp_path, monkeypatch):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    executor = LangChainTeamLeadExecutor(runtime=_ContractErrorRuntime())

    result = executor.run(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
    )

    assert result.delivery_state["status"] == "team_lead_sprint_blocked"
    assert "Team Lead AgentExecutor completed without calling any tool." in (
        result.delivery_state["blockers"][-1]
    )
    assert sprint_completion_state("run", "sprint-01").status == "blocked"
    assert get_work_item("run", "PLAN-04").status == "blocked"
    tool_names = [event.tool_name for event in repo.list_tool_call_events(run.id)]
    assert tool_names == ["block_sprint"]


def test_contract_error_without_work_item_id_does_not_write_runtime_tool_event(
    tmp_path, monkeypatch
):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(
        toolbox.codex_review(
            target_agent="",
            purpose="Diagnose status.",
            question="What happened?",
            artifact_refs="",
            intent="review_feedback",
            work_item_id="",
            reason="Diagnose status.",
            message="",
        )
    )

    assert payload["status"] == "contract_error"
    assert repo.list_tool_call_events(run.id, agent_id="team-lead-agent") == []
    assert toolbox.history == []


def test_team_lead_codex_review_contract_is_task_scoped():
    contract = TEAM_LEAD_TOOL_CONTRACT_REGISTRY.get("codex_review")

    assert "work_item_id" in contract.required_parameters


def test_team_lead_codex_review_uses_selected_codex_model(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _write_agent_runtime_env(state, {"AGENT_CODEX_MODEL": "gpt-5.4"})
    reviewer = _FakeHeadReviewer()
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
        codex_reviewer=reviewer,
    )

    toolbox.codex_review(
        work_item_id="US-1",
        purpose="Review feature.",
        question="Is this ready?",
        artifact_refs="",
        reason="Review feature.",
    )

    assert reviewer.last_request.model == "gpt-5.4"


def test_head_result_recomputes_blockers_from_db_not_stale_state(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    mark_sprint_done("run", "sprint-01")
    stale_state = {
        **state,
        "stage": "head",
        "status": "head_planning_blocked",
        "blockers": ["Fullstack work item US-1 did not complete successfully."],
    }

    write_head_result(stale_state, [])

    result = json.loads((tmp_path / "run" / "head" / "result.json").read_text(encoding="utf-8"))
    assert result["blockers"] == []


def test_team_lead_sprint_plan_artifact_is_registered_to_plan_04(tmp_path, monkeypatch):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)

    run_team_lead_agent_graph(
        state,
        workers=_team_lead_workers(),
        executor=_NoopTeamLeadExecutor(),
    )

    records = repo.list_artifact_records(run.id)
    sprint_plan = next(
        record for record in records if record.relative_path == "team-lead/sprint-01-plan.json"
    )
    assert sprint_plan.work_item_id == "PLAN-04"
    assert sprint_plan.artifact_type == "team_lead_plan"


def test_head_treats_completed_sprint_as_complete_not_missing_work_items(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _mark_work_item_done("US-1")
    mark_sprint_done("run", "sprint-01")
    toolbox = HeadToolbox(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=_head_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.run_team_lead("sprint-01", reason="Continue delivery."))

    assert "already complete" in payload["message"]
    assert "sprint-02" in payload["message"]
    assert next_sprint_to_run("run") == "sprint-02"


def test_head_does_not_finish_plan_04_when_non_final_sprint_only_started(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)

    def team_lead_started_worker(worker_state):
        mark_sprint_started("run", "sprint-01")
        return {**worker_state, "stage": "team_lead", "status": "team_lead_sprint_started"}

    workers = HeadWorkers(
        business_analyst=lambda worker_state: worker_state,
        architect=lambda worker_state: worker_state,
        project_manager=lambda worker_state: worker_state,
        team_lead=team_lead_started_worker,
    )
    toolbox = HeadToolbox(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=workers,
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.run_team_lead("sprint-01", reason="Start first sprint."))

    assert payload["status"] == "team_lead_sprint_started"
    assert sprint_completion_state("run", "sprint-01").status == "running"
    assert get_work_item("run", "PLAN-04").status == "in_progress"


def test_head_executor_blocks_if_runtime_stops_with_pending_db_sprint(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    executor = LangChainHeadExecutor(runtime=_StartingTeamLeadRuntime())

    result = executor.run(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=_head_workers_with_started_team_lead(),
        max_steps=5,
    )

    assert result.delivery_state["status"] == "head_planning_blocked"
    assert "Next DB sprint still pending: sprint-01" in result.delivery_state["blockers"][-1]
    assert sprint_completion_state("run", "sprint-01").status == "running"
    assert get_work_item("run", "PLAN-04").status == "blocked"


def test_head_delivery_status_inspection_is_scoped_to_plan_04(tmp_path, monkeypatch):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = HeadToolbox(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=_head_workers(),
        max_steps=5,
        history=[],
        status_inspector=_FakeStatusInspector(),
    )

    toolbox.inspect_delivery_status(reason="Check delivery status.")

    tool_calls = repo.list_tool_call_events(run.id, agent_id="head-agent")
    assert tool_calls[-1].tool_name == "inspect_delivery_status"
    assert tool_calls[-1].work_item_id == "PLAN-04"
    records = repo.list_artifact_records(run.id)
    status_result = next(
        record
        for record in records
        if record.relative_path == "head/status-inspections/fake/status.json"
    )
    assert status_result.work_item_id == "PLAN-04"


def test_head_codex_review_tool_call_is_scoped_to_planning_work_item(tmp_path, monkeypatch):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = HeadToolbox(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=_head_workers(),
        max_steps=5,
        history=[],
        codex_reviewer=_FakeHeadReviewer(),
    )

    toolbox.codex_review(
        purpose="Review architecture.",
        question="Is this ready?",
        artifact_refs="",
        correlation_id="PLAN-02",
        reason="Review architecture.",
    )

    tool_calls = repo.list_tool_call_events(run.id, agent_id="head-agent")
    assert tool_calls[-1].tool_name == "codex_review"
    assert tool_calls[-1].work_item_id == "PLAN-02"


def test_head_codex_review_uses_selected_codex_model(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _write_agent_runtime_env(state, {"AGENT_CODEX_MODEL": "gpt-5.4"})
    reviewer = _FakeHeadReviewer()
    toolbox = HeadToolbox(
        delivery_state={**state, "stage": "head", "status": "running"},
        workers=_head_workers(),
        max_steps=5,
        history=[],
        codex_reviewer=reviewer,
    )

    toolbox.codex_review(
        purpose="Review architecture.",
        question="Is this ready?",
        artifact_refs="",
        correlation_id="PLAN-02",
        reason="Review architecture.",
    )

    assert reviewer.last_request.model == "gpt-5.4"


def test_pm_materialization_marks_last_sprint_final_when_pm_omits_final_flag(
    tmp_path, monkeypatch
):
    _repo, _run, _state = _setup_runtime(tmp_path, monkeypatch)

    assert not sprint_is_final("run", "sprint-01")
    assert sprint_is_final("run", "sprint-02")


def test_complete_sprint_rejects_pending_db_work_items(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.complete_sprint("PLAN-04", reason="Premature close."))

    assert payload["status"] == "sprint_not_complete"
    assert sprint_completion_state("run", "sprint-01").status == "running"


def test_team_lead_rejects_second_active_sprint_work_item_before_request(
    tmp_path, monkeypatch
):
    repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    _add_work_item(repo, "US-1B", sprint_id="sprint-01", delivery_order=2)
    record_work_item_transition(
        ToolExecutionRecord(
            run_id="run",
            work_item_id="US-1",
            sprint_id="sprint-01",
            owner_agent="fullstack-agent",
            tool_name="run_fullstack",
            tool_call_id="run:fullstack:us-1",
            attempt_id="1",
            status="in_progress",
            activity_message="Fullstack started US-1.",
        )
    )
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.run_fullstack("US-1B", reason="Start second item."))

    assert payload["status"] == "work_item_precondition_failed"
    assert "US-1 is already active" in payload["business_summary"]
    assert get_work_item("run", "US-1B").status == "todo"
    assert not (tmp_path / "run" / "team-lead" / "requests").exists()


def test_team_lead_worker_without_correlated_response_blocks_work_item(
    tmp_path, monkeypatch
):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(toolbox.run_fullstack("US-1", reason="Implement item."))

    assert payload["status"] == "worker_contract_error"
    assert get_work_item("run", "US-1").status == "blocked"


def test_team_lead_worker_records_external_reference_in_tool_call(
    tmp_path, monkeypatch
):
    repo, run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(
        toolbox.run_fullstack(
            "US-1",
            reason="Implement item.",
            external_reference='{"system":"github","type":"issue","id":"123"}',
        )
    )

    event = repo.list_tool_call_events(run.id, agent_id="team-lead-agent")[0]
    assert payload["status"] == "worker_contract_error"
    assert event.input_summary["external_reference"] == {
        "system": "github",
        "type": "issue",
        "id": "123",
    }


def test_team_lead_rejects_malformed_external_reference(tmp_path, monkeypatch):
    _repo, _run, state = _setup_runtime(tmp_path, monkeypatch)
    toolbox = TeamLeadToolbox(
        delivery_state=state,
        sprint={"sprint_id": "sprint-01"},
        workers=_team_lead_workers(),
        max_steps=5,
        history=[],
    )

    payload = json.loads(
        toolbox.run_fullstack("US-1", reason="Implement item.", external_reference="issue-123")
    )

    assert payload["status"] == "contract_error"
    assert "external_reference must be a JSON object string" in payload["business_summary"]
    assert get_work_item("run", "US-1").status == "todo"


class _CompletingRuntime:
    def invoke(self, request):
        for tool in request.tools:
            if tool.__name__ == "complete_sprint":
                return tool("PLAN-04", "Sprint handoff accepted.", "")
        raise AssertionError("complete_sprint tool was not available")


class _ContractErrorRuntime:
    def invoke(self, request):
        for tool in request.tools:
            if tool.__name__ == "codex_review":
                return tool(
                    target_agent="",
                    purpose="Diagnose sprint status inspection failure.",
                    question="Why did inspection fail?",
                    artifact_refs="",
                    intent="review_feedback",
                    work_item_id="",
                    reason="Diagnose sprint status inspection failure.",
                    message="",
                )
        raise AssertionError("codex_review tool was not available")


class _StartingTeamLeadRuntime:
    def invoke(self, request):
        for tool in request.tools:
            if tool.__name__ == "run_team_lead":
                return tool("sprint-01", "Start sprint.", "")
        raise AssertionError("run_team_lead tool was not available")


class _NoopTeamLeadExecutor:
    def run(
        self,
        *,
        delivery_state,
        sprint,
        workers,
        max_steps,
    ):
        return TeamLeadExecutorResult(delivery_state, [])


class _FakeStatusInspector:
    def run(self, request):
        root = request.run_dir / "head" / "status-inspections" / "fake"
        root.mkdir(parents=True)
        files = {
            "status.json": '{"status": "inspected", "delivery_status": "ready_for_next_sprint"}',
            "summary.md": "inspected",
            "prompt.md": "prompt",
            "execution.log": "log",
            "events.jsonl": "",
        }
        for name, content in files.items():
            (root / name).write_text(content + "\n", encoding="utf-8")
        return StatusInspectionResult(
            status="inspected",
            payload={"status": "inspected", "delivery_status": "ready_for_next_sprint"},
            artifact_refs=[],
            result_artifact="head/status-inspections/fake/status.json",
            summary_artifact="head/status-inspections/fake/summary.md",
            prompt_artifact="head/status-inspections/fake/prompt.md",
            log_artifact="head/status-inspections/fake/execution.log",
            raw_events_artifact="head/status-inspections/fake/events.jsonl",
            execution_id="fake-inspection",
            codex_thread_id="thread-fake",
        )


class _FakeHeadReviewer:
    def __init__(self):
        self.last_request = None

    def run(self, request):
        self.last_request = request
        root = request.run_dir / "head" / "codex-review" / "fake"
        root.mkdir(parents=True)
        files = {
            "summary.md": "reviewed",
            "prompt.md": "prompt",
            "execution.log": "log",
            "events.jsonl": "",
        }
        for name, content in files.items():
            (root / name).write_text(content + "\n", encoding="utf-8")
        return CodexReviewResult(
            status="reviewed",
            content="reviewed",
            artifact_refs=[],
            summary_artifact="head/codex-review/fake/summary.md",
            prompt_artifact="head/codex-review/fake/prompt.md",
            log_artifact="head/codex-review/fake/execution.log",
            raw_events_artifact="head/codex-review/fake/events.jsonl",
            execution_id="fake-review",
            codex_thread_id="thread-review",
        )


def _setup_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / "console.db"
    run_dir = tmp_path / "run"
    pm_dir = run_dir / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "release-plan.json").write_text(
        json.dumps(
            {
                "sprints": [
                    {"sprint_id": "sprint-01", "title": "Sprint 01", "delivery_order": 1},
                    {"sprint_id": "sprint-02", "title": "Sprint 02", "delivery_order": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    (pm_dir / "planned-work-items.json").write_text(
        json.dumps(
            [
                {
                    "id": "US-1",
                    "title": "First feature",
                    "sprint_id": "sprint-01",
                    "delivery_order": 1,
                    "suggested_owner_agent": "fullstack-agent",
                },
                {
                    "id": "US-2",
                    "title": "Second feature",
                    "sprint_id": "sprint-02",
                    "delivery_order": 1,
                    "suggested_owner_agent": "fullstack-agent",
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENTIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="run@example.test",
        username="runtime-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Runtime",
        request_text="Runtime",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    materialize_planning_items("run")
    materialize_pm_work_items("run")
    state = {
        "run_id": "run",
        "run_dir": str(run_dir),
        "stage": "team_lead",
        "status": "running",
        "blockers": [],
    }
    return repo, run, state


def _write_agent_runtime_env(state, values):
    env_path = Path(state["run_dir"]) / "delivery" / "agent-runtime.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )


def _mark_work_item_done(work_item_id):
    record_work_item_transition(
        ToolExecutionRecord(
            run_id="run",
            work_item_id=work_item_id,
            sprint_id="sprint-01",
            owner_agent="qa-agent",
            tool_name="run_qa",
            tool_call_id=f"run:qa:{work_item_id}",
            attempt_id="1",
            status="done",
            activity_message=f"QA passed {work_item_id}.",
        )
    )


def _add_work_item(repo, work_item_id, *, sprint_id, delivery_order):
    with repo.connect() as conn:
        row = conn.execute("SELECT id FROM runs WHERE run_uid = ?", ("run",)).fetchone()
        conn.execute(
            """
            INSERT INTO work_items (
                run_id, work_item_id, title, sprint_id, delivery_order, status,
                lane, owner_agent, assigned_agent, active, source_refs, artifact_ids,
                blocker, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'todo', 'todo', 'fullstack-agent',
                'fullstack-agent', 0, '[]', '[]', '', '2026-01-01T00:00:00Z',
                '2026-01-01T00:00:00Z')
            """,
            (int(row["id"]), work_item_id, work_item_id, sprint_id, delivery_order),
        )


def _team_lead_workers():
    def worker(state):
        return state

    return TeamLeadWorkers(
        fullstack=worker,
        qa=worker,
        deployment=worker,
        handoff=worker,
    )


def _head_workers():
    def fail_worker(state):
        raise AssertionError("Head must not route task work for an already completed sprint")

    return HeadWorkers(
        business_analyst=fail_worker,
        architect=fail_worker,
        project_manager=fail_worker,
        team_lead=fail_worker,
    )


def _head_workers_with_started_team_lead():
    def planning_worker(state):
        return state

    def team_lead_started_worker(state):
        mark_sprint_started("run", "sprint-01")
        return {**state, "stage": "team_lead", "status": "team_lead_sprint_started"}

    return HeadWorkers(
        business_analyst=planning_worker,
        architect=planning_worker,
        project_manager=planning_worker,
        team_lead=team_lead_started_worker,
    )
