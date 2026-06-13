"""Codex JSON event normalization and rendering."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_company.integrations.commands import timestamp_now
from agentic_company.platform.security import redact_sensitive_output


def append_raw_codex_event(
    raw_events_path: Path,
    line: str,
    *,
    metadata: dict[str, str] | None = None,
) -> dict[str, object] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    event = _redact_event(_repair_event(parsed))
    if isinstance(event, dict) and metadata:
        event.update({key: value for key, value in metadata.items() if value})
    event.setdefault("recorded_at", timestamp_now())
    with raw_events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event if isinstance(event, dict) else None


def write_structured_codex_artifacts(
    run_dir: Path,
    output: str,
    *,
    raw_events_filename: str,
) -> list[str]:
    events: list[dict[str, object]] = []

    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            event = _redact_event(_repair_event(parsed))
            event.setdefault("recorded_at", timestamp_now())
            events.append(event)

    written: list[str] = []
    if events:
        raw_path = run_dir / raw_events_filename
        if not raw_path.exists() or not raw_path.read_text(encoding="utf-8").strip():
            raw_path.write_text(
                "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events)
                + "\n",
                encoding="utf-8",
            )
        written.append(raw_events_filename)

    return written


def extract_codex_usage(raw_events_path: Path) -> tuple[int | None, int | None]:
    """Return ``(input_tokens, output_tokens)`` from a Codex raw-events file.

    Codex reports cumulative token usage in its events; the last reported values
    win. Returns ``(None, None)`` when no usage is present.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    if not raw_events_path.exists():
        return input_tokens, output_tokens
    try:
        lines = raw_events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return input_tokens, output_tokens
    for line in lines:
        stripped = line.strip()
        if not stripped or "input_tokens" not in stripped and "output_tokens" not in stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        usage = _find_token_usage(event)
        if usage is None:
            continue
        if isinstance(usage.get("input_tokens"), int):
            input_tokens = usage["input_tokens"]
        if isinstance(usage.get("output_tokens"), int):
            output_tokens = usage["output_tokens"]
    return input_tokens, output_tokens


def raw_events_artifact_link(output_artifacts: list[str]) -> str:
    """Return the run-relative Codex raw-events artifact link, if present."""

    for artifact in output_artifacts:
        if artifact.replace("\\", "/").endswith("events.jsonl"):
            return artifact
    return ""


def codex_usage_from_artifacts(
    run_dir: Path,
    output_artifacts: list[str],
) -> tuple[int | None, int | None]:
    """Return Codex token usage from the raw-events artifact in a result."""

    raw_events = raw_events_artifact_link(output_artifacts)
    if not raw_events:
        return None, None
    return extract_codex_usage(run_dir / raw_events)


def _find_token_usage(value: object) -> dict[str, object] | None:
    """Find a dict carrying input_tokens/output_tokens anywhere in the event."""

    if isinstance(value, dict):
        if "input_tokens" in value or "output_tokens" in value:
            return value
        for item in value.values():
            found = _find_token_usage(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_token_usage(item)
            if found is not None:
                return found
    return None


def summarize_codex_event(event: dict[str, object]) -> str | None:
    method = _normalized_event_type(event)
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    item = params.get("item") if isinstance(params.get("item"), dict) else event.get("item")
    item = item if isinstance(item, dict) else {}
    item_type = _normalized_item_type(item)

    if method == "thread.started":
        thread_id = event.get("thread_id") or params.get("threadId") or params.get("thread_id")
        return f"Thread started{f': {thread_id}' if thread_id else ''}"
    if method == "turn.started":
        return "Turn started"
    if method == "turn.completed":
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        status = turn.get("status") or event.get("status") or "completed"
        return f"Turn {status}"
    if method == "turn.diff.updated":
        return "Diff updated"
    if method == "thread.status.changed":
        status = params.get("status") if isinstance(params.get("status"), dict) else {}
        if isinstance(status, dict):
            return f"Thread status: {status.get('type', 'unknown')}"
        return "Thread status changed"
    if method == "item.started":
        if item_type == "command_execution":
            return f"Running command: {item.get('command', '')}"
        if item_type == "file_change":
            return "Applying file changes"
        if item_type == "agent_message":
            return "Agent message started"
        return f"Started {item_type}"
    if method == "item.completed":
        if item_type == "command_execution":
            return f"Command finished with status {item.get('status', 'completed')}"
        if item_type == "file_change":
            return f"File changes {item.get('status', 'completed')}"
        if item_type == "agent_message":
            return "Agent message completed"
        return f"Completed {item_type}"
    if method == "item.agent_message.delta":
        return None
    if method == "item.command_execution.output_delta":
        return None
    if method:
        return method
    return None


def parse_codex_event_sections(events: list[dict[str, object]]) -> dict[str, str]:
    summary_lines: list[str] = []
    commentary_parts: list[str] = []
    final_parts: list[str] = []
    command_parts: list[str] = []
    diff_parts: list[str] = []

    for event in events:
        summary = summarize_codex_event(event)
        if summary:
            summary_lines.append(summary)

        item = _event_item(event)
        item_type = _normalized_item_type(item)
        method = _normalized_event_type(event)
        if method == "item.completed" and item_type == "agent_message":
            text = _clean_text(str(item.get("text", ""))).strip()
            if text:
                commentary_parts.append(text)
            continue
        if method == "item.completed" and item_type == "command_execution":
            command_parts.append(_render_command_item(item))
            continue
        if method in {"item.started", "item.completed"} and item_type == "file_change":
            changes = _render_file_changes(item)
            if changes:
                diff_parts.append(changes)
            continue

        delta = _event_delta(event)
        if delta:
            if _event_phase(event) == "final_answer":
                final_parts.append(_clean_text(delta))
            else:
                commentary_parts.append(_clean_text(delta))

        command_delta = _command_delta(event)
        if command_delta:
            command_parts.append(_clean_text(command_delta))

        diff = _diff_delta(event)
        if diff:
            diff_parts.append(_clean_text(diff))

    commentary_sections: list[str] = []
    commentary = "\n\n".join(part.strip() for part in commentary_parts if part.strip())
    final = "\n\n".join(part.strip() for part in final_parts if part.strip())
    if commentary:
        commentary_sections.append("## Commentary\n\n" + commentary)
    if final:
        commentary_sections.append("## Final Answer\n\n" + final)

    return {
        "events": "\n".join(f"- {line}" for line in summary_lines),
        "commentary": "\n\n".join(commentary_sections),
        "command": "\n".join(part.rstrip() for part in command_parts if part.strip()).strip(),
        "diff": "\n\n".join(part.strip() for part in diff_parts if part.strip()),
    }


def render_raw_codex_events(events: list[dict[str, object]]) -> str:
    return "\n".join(
        json.dumps(_repair_event(event), ensure_ascii=False, sort_keys=True) for event in events
    )


def _redact_event(value: object) -> object:
    if isinstance(value, str):
        return redact_sensitive_output(value)
    if isinstance(value, list):
        return [_redact_event(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_event(item) for key, item in value.items()}
    return value


def _event_delta(event: dict[str, object]) -> str:
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    delta = params.get("delta") or event.get("delta") or ""
    return _clean_text(str(delta))


def _event_phase(event: dict[str, object]) -> str:
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    item = params.get("item") if isinstance(params.get("item"), dict) else event.get("item")
    if isinstance(item, dict):
        return str(item.get("phase", "commentary"))
    return str(params.get("phase") or event.get("phase") or "commentary")


def _command_delta(event: dict[str, object]) -> str:
    method = _normalized_event_type(event)
    if method != "item.command_execution.output_delta":
        return ""
    return _event_delta(event)


def _diff_delta(event: dict[str, object]) -> str:
    method = _normalized_event_type(event)
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    if method != "turn.diff.updated":
        return ""
    return _clean_text(str(params.get("diff") or event.get("diff") or ""))


def _event_item(event: dict[str, object]) -> dict[str, object]:
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    item = params.get("item") if isinstance(params.get("item"), dict) else event.get("item")
    return item if isinstance(item, dict) else {}


def _normalized_event_type(event: dict[str, object]) -> str:
    raw = str(event.get("method") or event.get("type") or "")
    normalized = raw.replace("/", ".")
    normalized = normalized.replace("agentMessage", "agent_message")
    normalized = normalized.replace("commandExecution", "command_execution")
    normalized = normalized.replace("outputDelta", "output_delta")
    return normalized


def _normalized_item_type(item: dict[str, object]) -> str:
    raw = str(item.get("type", "item"))
    return raw.replace("commandExecution", "command_execution").replace(
        "agentMessage", "agent_message"
    )


def _render_command_item(item: dict[str, object]) -> str:
    command = _clean_text(str(item.get("command", ""))).strip()
    output = _clean_text(str(item.get("aggregated_output", ""))).strip()
    exit_code = item.get("exit_code")
    status = item.get("status", "completed")
    lines = [f"$ {command}" if command else "$ <unknown command>"]
    if output:
        lines.append(output)
    lines.append(f"status={status} exit_code={exit_code}")
    return "\n".join(lines) + "\n"


def _render_file_changes(item: dict[str, object]) -> str:
    changes = item.get("changes")
    if not isinstance(changes, list):
        return ""

    lines = ["File changes:"]
    for change in changes:
        if not isinstance(change, dict):
            continue
        kind = _clean_text(str(change.get("kind", "change")))
        path = _clean_text(str(change.get("path", "")))
        lines.append(f"- {kind}: {path}")
    return "\n".join(lines)


def _repair_event(value: object) -> object:
    if isinstance(value, dict):
        return {key: _repair_event(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_repair_event(item) for item in value]
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _clean_text(value: str) -> str:
    replacements = {
        "\u00e2\u20ac\u2122": "\u2019",
        "\u00e2\u20ac\u02dc": "\u2018",
        "\u00e2\u20ac\u0153": "\u201c",
        "\u00e2\u20ac\ufffd": "\u201d",
        '\u00e2\u20ac"': "-",
        "\u0101\u20ac\u2122": "\u2019",
        "\u0101\u20ac\u02dc": "\u2018",
        "\u0101\u20ac\u0153": "\u201c",
        "\u0101\u20ac\ufffd": "\u201d",
        '\u0101\u20ac"': "-",
    }
    for broken, repaired in replacements.items():
        value = value.replace(broken, repaired)
    if "\u00c3\u00a2" not in value and "\u00c3\u0192" not in value:
        return value
    try:
        return value.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return value
