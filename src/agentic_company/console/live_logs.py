"""Pure helpers for rendering console live-log activity."""

from __future__ import annotations

from pathlib import Path


def friendly_log_entries(
    events: list[dict[str, object]],
    codex_events: list[dict[str, object]],
    *,
    qa_log: Path,
    deployment_log: Path,
) -> list[str]:
    entries = [
        *_workflow_event_entries(events),
        *_codex_commentary_entries(codex_events),
        *command_progress_entries(qa_log, agent="qa-agent", phase="QA"),
        *command_progress_entries(
            deployment_log,
            agent="deployment-agent",
            phase="Deployment",
        ),
    ]
    return [entry for _, entry in sorted(entries, key=lambda entry: entry[0])[-180:]]


def command_progress_entries(path: Path, *, agent: str, phase: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for index, block in enumerate(_command_log_blocks(path)):
        title = block.get("heading") or _short_command(block.get("command", ""))
        if not title:
            continue
        started_at = block.get("started_at", "")
        completed_at = block.get("completed_at", "")
        exit_code = block.get("exit_code", "")
        status = _command_block_status(block)
        if started_at:
            entries.append(
                (
                    f"{started_at}.100.{index:04d}",
                    f"**{_display_timestamp(started_at)} - {phase} step started**\n\n"
                    f"`{agent}` | {title}",
                )
            )
        if completed_at or exit_code:
            timestamp = completed_at or started_at
            if timestamp:
                entries.append(
                    (
                        f"{timestamp}.900.{index:04d}",
                        f"**{_display_timestamp(timestamp)} - {phase} step {status}**\n\n"
                        f"`{agent}` | {title}",
                    )
                )
    return entries


def _codex_commentary_entries(events: list[dict[str, object]]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for index, event in enumerate(events):
        text = _codex_message_text(event)
        if not text:
            continue
        event_timestamp = _event_timestamp(event)
        sort_key = event_timestamp or f"9999-codex-{index:05d}"
        display_time = _display_timestamp(event_timestamp)
        entries.append(
            (
                sort_key,
                f"**{display_time} - Codex**\n\n{_indent_multiline_markdown(text)}",
            )
        )
    return entries


def _workflow_event_entries(events: list[dict[str, object]]) -> list[tuple[str, str]]:
    friendly_names = {
        "delivery_graph_started": "Delivery graph started",
        "delivery_graph_completed": "Delivery graph completed",
        "delivery_graph_failed": "Delivery graph failed",
        "delivery_graph_state_written": "Graph state saved",
        "delivery_graph_node_started": "Graph node started",
        "delivery_graph_node_completed": "Graph node completed",
        "delivery_graph_node_failed": "Graph node failed",
        "run_started": "Planning started",
        "run_completed": "Planning completed",
        "execution_started": "Fullstack Agent started",
        "execution_completed": "Fullstack Agent completed",
        "qa_started": "QA started",
        "qa_completed": "QA completed",
        "deployment_started": "Azure deployment started",
        "deployment_completed": "Azure deployment completed",
        "handoff_started": "Handoff started",
        "handoff_ready": "Handoff ready",
        "fix_request_created": "QA repair request created",
    }
    entries: list[tuple[str, str]] = []
    for event in events:
        name = str(event.get("event", ""))
        label = friendly_names.get(name)
        if not label:
            continue
        timestamp = str(event.get("timestamp", ""))
        agent = str(event.get("agent_id", ""))
        suffix = _event_suffix(event)
        entries.append(
            (
                timestamp,
                f"**{_display_timestamp(timestamp)} - {label}**\n\n`{agent}`{suffix}",
            )
        )
    return entries


def _command_log_blocks(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = {"heading": line.removeprefix("## ").strip()}
            continue
        if line.startswith("$ "):
            if current and "command" in current:
                blocks.append(current)
                current = {}
            current["command"] = line.removeprefix("$ ").strip()
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            if key in {"started_at", "completed_at", "exit_code", "status"}:
                current[key] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def _command_block_status(block: dict[str, str]) -> str:
    status = block.get("status", "")
    if status and status != "running":
        return status
    exit_code = block.get("exit_code", "")
    if exit_code == "0":
        return "passed"
    if exit_code:
        return "failed"
    return "running"


def _short_command(command: str) -> str:
    if not command:
        return ""
    if len(command) <= 90:
        return command
    return command[:87] + "..."


def _codex_message_text(event: dict[str, object]) -> str:
    method = _codex_event_name(event)
    if method != "item.completed":
        return ""
    item = _codex_event_item(event)
    item_type = str(item.get("type", "")).replace("agentMessage", "agent_message")
    if item_type != "agent_message":
        return ""
    return _strip_codex_headings(str(item.get("text", "")).strip())


def _codex_event_name(event: dict[str, object]) -> str:
    name = str(event.get("type") or event.get("method") or "")
    return name.replace("/", ".")


def _codex_event_item(event: dict[str, object]) -> dict[str, object]:
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    item = params.get("item") if isinstance(params.get("item"), dict) else event.get("item")
    return item if isinstance(item, dict) else {}


def _strip_codex_headings(text: str) -> str:
    stripped_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"## Commentary", "## Final Answer"}:
            continue
        stripped_lines.append(line)
    return "\n".join(stripped_lines).strip()


def _indent_multiline_markdown(text: str) -> str:
    return "\n".join("> " + line if line.strip() else ">" for line in text.splitlines())


def _event_timestamp(event: dict[str, object]) -> str:
    return str(event.get("timestamp") or event.get("recorded_at") or "")


def _display_timestamp(timestamp: str) -> str:
    if "T" in timestamp:
        date, time = timestamp.split("T", 1)
        return f"{date} {time[:8]}"
    return timestamp[:19] if timestamp else "--:--:--"


def _event_suffix(event: dict[str, object]) -> str:
    data = event.get("data", {})
    if not isinstance(data, dict):
        return ""
    node = data.get("node")
    if node:
        status = data.get("status")
        return f" | node={node}" + (f" status={status}" if status else "")
    status = data.get("status")
    artifact = data.get("artifact")
    if status:
        return f" | status={status}"
    if artifact:
        return f" | {artifact}"
    return ""
