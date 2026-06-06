import json
import subprocess
from pathlib import Path

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.codex_review import CodexReviewRequest, CodexReviewRunner
from agentic_company.platform.events import write_event
from agentic_company.platform.run_trace import (
    MODEL_CALL_EVENTS_FILE,
    RUN_EVENTS_FILE,
    RUN_TRACE_DIR,
    TOOL_CALL_EVENTS_FILE,
    load_model_call_events,
    load_run_events,
    load_tool_call_events,
    record_model_call_event,
    record_tool_call_event,
    sanitize_trace_data,
    trace_summary,
)
from agentic_company.platform.status_inspector import StatusInspectionRequest, StatusInspectorRunner


def test_write_event_records_structured_trace_without_retired_root_log(tmp_path: Path):
    event_log = tmp_path / "events.jsonl"

    write_event(
        event_log,
        "run-1",
        "builder",
        "tool_completed",
        {
            "status": "ready",
            "message": "Built",
            "api_key": "sk-secret",
            "artifact": {"artifact_id": "art_abc123"},
        },
    )

    events = load_run_events(tmp_path)

    assert not event_log.exists()
    assert events[0].event_type == "tool_completed"
    assert events[0].status == "ready"
    assert events[0].data["api_key"] == "[REDACTED]"
    assert events[0].artifact_ids == ["art_abc123"]
    assert (tmp_path / RUN_TRACE_DIR / RUN_EVENTS_FILE).exists()


def test_tool_and_model_trace_roundtrip_and_summary(tmp_path: Path):
    record_tool_call_event(
        tmp_path,
        run_id="run-1",
        agent_id="team-lead-agent",
        tool_name="run_fullstack",
        tool_call_id="call-1",
        status="codex_completed",
        input_summary={"target": "F1"},
        output_summary={"output_artifacts": [{"artifact_id": "art_tool"}]},
        duration_ms=42,
    )
    record_model_call_event(
        tmp_path,
        run_id="run-1",
        agent_id="fullstack-agent",
        provider="openai",
        model="gpt-5.5",
        purpose="codex_exec",
        prompt_ref="fullstack/prompt.md",
        status="codex_completed",
        duration_ms=41,
    )

    tool_events = load_tool_call_events(tmp_path)
    model_events = load_model_call_events(tmp_path)
    summary = trace_summary([], tool_events, model_events)

    assert (tmp_path / RUN_TRACE_DIR / TOOL_CALL_EVENTS_FILE).exists()
    assert (tmp_path / RUN_TRACE_DIR / MODEL_CALL_EVENTS_FILE).exists()
    assert tool_events[0].artifact_ids == ["art_tool"]
    assert model_events[0].estimated_cost_usd is None
    assert summary["duration_ms"] == 83
    assert summary["tools"] == {"run_fullstack": 1}


def test_trace_events_are_persisted_to_console_db(tmp_path: Path, monkeypatch):
    repo = _db_repo(tmp_path, monkeypatch)
    user = repo.create_user(
        email="trace@example.test",
        username="trace-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Trace",
        request_text="Trace",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-trace-db",
        run_dir=tmp_path,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )

    write_event(
        tmp_path,
        "run-trace-db",
        "delivery-graph",
        "delivery_graph_started",
        {"status": "running"},
    )
    record_tool_call_event(
        tmp_path,
        run_id="run-trace-db",
        agent_id="qa-agent",
        tool_name="run_qa",
        tool_call_id="qa-1",
        status="qa_passed",
        work_item_id="US-accounts",
        artifact_ids=["art_qa"],
    )
    record_model_call_event(
        tmp_path,
        run_id="run-trace-db",
        agent_id="qa-agent",
        provider="openai",
        model="gpt-5.5",
        purpose="codex_exec",
        prompt_ref="qa/prompt.md",
        status="qa_passed",
    )

    assert repo.list_run_events(run.id)[0].event_type == "delivery_graph_started"
    assert repo.list_tool_call_events(run.id)[0].work_item_id == "US-accounts"
    assert repo.list_tool_call_events(run.id)[0].artifact_ids == ["art_qa"]
    assert repo.list_model_call_events(run.id)[0].model == "gpt-5.5"

    for filename in (RUN_EVENTS_FILE, TOOL_CALL_EVENTS_FILE, MODEL_CALL_EVENTS_FILE):
        (tmp_path / RUN_TRACE_DIR / filename).unlink()

    assert load_run_events(tmp_path)[0].event_type == "delivery_graph_started"
    assert load_tool_call_events(tmp_path)[0].work_item_id == "US-accounts"
    assert load_model_call_events(tmp_path)[0].model == "gpt-5.5"


def test_codex_agent_message_run_events_are_card_logs(tmp_path: Path, monkeypatch):
    repo = _db_repo(tmp_path, monkeypatch)
    user = repo.create_user(
        email="cardlog@example.test",
        username="cardlog",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Card log",
        request_text="Build",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-card-log",
        run_dir=tmp_path,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )

    write_event(
        tmp_path,
        "run-card-log",
        "business-analyst-agent",
        "codex_agent_message",
        {
            "status": "in_progress",
            "message": "I am preparing the requirements brief.",
            "work_item_id": "PLAN-01",
        },
    )
    write_event(
        tmp_path,
        "run-card-log",
        "business-analyst-agent",
        "codex_command_started",
        {
            "status": "in_progress",
            "message": "Started command: Get-Content requirements",
            "work_item_id": "PLAN-01",
        },
    )

    events = repo.list_activity_events(run.id, work_item_id="PLAN-01")

    assert [event.message for event in events] == ["I am preparing the requirements brief."]
    assert events[0].tool_name == "codex_agent_message"


def test_trace_sanitizer_redacts_secret_like_fields():
    assert sanitize_trace_data(
        {
            "OPENAI_API_KEY": "sk-secret",
            "nested": {"token": "secret-token", "normal": "visible"},
        }
    ) == {
        "OPENAI_API_KEY": "[REDACTED]",
        "nested": {"token": "[REDACTED]", "normal": "visible"},
    }


def test_codex_review_runner_records_model_trace_and_registers_artifacts(
    tmp_path: Path, monkeypatch
):
    repo = _db_repo(tmp_path, monkeypatch)
    user = repo.create_user(
        email="review@example.test",
        username="review-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Review",
        request_text="Review",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-review",
        run_dir=tmp_path,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    runner = CodexReviewRunner(command_executor=_review_command)

    result = runner.run(
        CodexReviewRequest(
            run_id="run-review",
            run_dir=tmp_path,
            requesting_agent="head-agent",
            purpose="Review artifacts.",
            question="Is this ready?",
            model="gpt-5.5",
        )
    )

    events = load_model_call_events(tmp_path)
    artifacts = [
        record
        for record in repo.list_artifact_records(run.id)
        if record.owner_agent == "head-agent"
    ]

    assert result.status == "reviewed"
    assert events[0].provider == "openai"
    assert events[0].model == "gpt-5.5"
    assert events[0].purpose == "codex_review"
    assert events[0].prompt_ref == result.prompt_artifact
    assert events[0].estimated_cost_usd is None
    assert {artifact.relative_path for artifact in artifacts} >= {
        result.summary_artifact,
        result.prompt_artifact,
        result.log_artifact,
        result.raw_events_artifact,
    }
    assert all(artifact.visibility == "developer" for artifact in artifacts)


def test_status_inspector_runner_records_model_trace_and_registers_artifacts(
    tmp_path: Path, monkeypatch
):
    repo = _db_repo(tmp_path, monkeypatch)
    user = repo.create_user(
        email="status@example.test",
        username="status-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Status",
        request_text="Status",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-status",
        run_dir=tmp_path,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    runner = StatusInspectorRunner(command_executor=_status_command)

    result = runner.run(
        StatusInspectionRequest(
            run_id="run-status",
            run_dir=tmp_path,
            requesting_agent="team-lead-agent",
            scope="sprint",
            purpose="Inspect sprint.",
            status_context={"sprint_id": "sprint-01"},
            model="gpt-5.5",
        )
    )

    events = load_model_call_events(tmp_path)
    artifacts = [
        record
        for record in repo.list_artifact_records(run.id)
        if record.owner_agent == "team-lead-agent"
    ]

    assert result.status == "inspected"
    assert events[0].provider == "openai"
    assert events[0].model == "gpt-5.5"
    assert events[0].purpose == "status_inspection"
    assert events[0].prompt_ref == result.prompt_artifact
    assert events[0].estimated_cost_usd is None
    assert {artifact.relative_path for artifact in artifacts} >= {
        result.result_artifact,
        result.summary_artifact,
        result.prompt_artifact,
        result.log_artifact,
        result.raw_events_artifact,
    }
    assert all(artifact.visibility == "developer" for artifact in artifacts)


def _review_command(
    command: list[str] | tuple[str, ...],
    prompt: str,
    timeout_seconds: int,
    log_path: Path,
    raw_events_path: Path,
) -> subprocess.CompletedProcess[str]:
    summary_path = log_path.parent / "summary.md"
    summary_path.write_text("Review OK.", encoding="utf-8")
    log_path.write_text("log", encoding="utf-8")
    raw_events_path.write_text("", encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, stdout="Review OK.", stderr="")


def _status_command(
    command: list[str] | tuple[str, ...],
    prompt: str,
    timeout_seconds: int,
    log_path: Path,
    raw_events_path: Path,
) -> subprocess.CompletedProcess[str]:
    status_path = log_path.parent / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "inspected",
                "scope": "sprint",
                "can_complete_sprint": True,
                "status_summary": "Ready.",
            }
        ),
        encoding="utf-8",
    )
    summary_path = log_path.parent / "summary.md"
    summary_path.write_text("Ready.", encoding="utf-8")
    log_path.write_text("log", encoding="utf-8")
    raw_events_path.write_text("", encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, stdout="Ready.", stderr="")


def _db_repo(tmp_path: Path, monkeypatch) -> ConsoleRepository:
    db_path = tmp_path / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    return repo
