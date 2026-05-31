import json
import subprocess
from pathlib import Path

from agentic_company.platform.artifact_registry import list_artifacts
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


def test_write_event_records_structured_trace_without_legacy_root_log(tmp_path: Path):
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
        model="gpt-5.3-codex",
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


def test_codex_review_runner_records_model_trace_and_registers_artifacts(tmp_path: Path):
    runner = CodexReviewRunner(command_executor=_review_command)

    result = runner.run(
        CodexReviewRequest(
            run_id="run-review",
            run_dir=tmp_path,
            requesting_agent="head-agent",
            purpose="Review artifacts.",
            question="Is this ready?",
            model="gpt-5.3-codex",
        )
    )

    events = load_model_call_events(tmp_path)
    artifacts = list_artifacts(tmp_path, owner_agent="head-agent")

    assert result.status == "reviewed"
    assert events[0].provider == "openai"
    assert events[0].model == "gpt-5.3-codex"
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


def test_status_inspector_runner_records_model_trace_and_registers_artifacts(tmp_path: Path):
    runner = StatusInspectorRunner(command_executor=_status_command)

    result = runner.run(
        StatusInspectionRequest(
            run_id="run-status",
            run_dir=tmp_path,
            requesting_agent="team-lead-agent",
            scope="sprint",
            purpose="Inspect sprint.",
            status_context={"sprint_id": "sprint-01"},
            model="gpt-5.3-codex",
        )
    )

    events = load_model_call_events(tmp_path)
    artifacts = list_artifacts(tmp_path, owner_agent="team-lead-agent")

    assert result.status == "inspected"
    assert events[0].provider == "openai"
    assert events[0].model == "gpt-5.3-codex"
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
