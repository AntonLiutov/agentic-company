import json
from pathlib import Path

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


def test_write_event_keeps_legacy_log_and_mirrors_structured_trace(tmp_path: Path):
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

    legacy = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
    events = load_run_events(tmp_path)

    assert legacy[0]["event"] == "tool_completed"
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
