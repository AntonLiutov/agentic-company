"""Live log rendering for the Streamlit operator console."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from agentic_company.console.live_logs import friendly_log_entries
from agentic_company.console.support import read_events
from agentic_company.integrations.codex.events import (
    parse_codex_event_sections,
    render_raw_codex_events,
)
from agentic_company.platform.security import redact_sensitive_output


def render_live_logs(run_dir: Path) -> None:
    """Render one friendly log stream plus collapsible raw developer evidence."""

    events = read_events(run_dir)
    event_lines = [
        f"{event.get('timestamp', '')} {event.get('agent_id', '')} {event.get('event', '')}"
        for event in events
    ]
    codex_events_path = run_dir / "codex" / "events.jsonl"
    codex_events = _read_jsonl(codex_events_path)
    codex_sections = parse_codex_event_sections(codex_events)
    raw_codex = (
        redact_sensitive_output(render_raw_codex_events(codex_events)) if codex_events else ""
    )
    qa_log = run_dir / "qa" / "commands.log"
    docker_log = run_dir / "qa" / "docker" / "runtime-command.log"
    deployment_log = run_dir / "deployment" / "commands.log"
    codex_log = run_dir / "codex" / "execution.log"
    friendly_entries = friendly_log_entries(
        events,
        codex_events,
        qa_log=qa_log,
        deployment_log=deployment_log,
    )
    raw_sections = [
        _log_section("Workflow events", "\n".join(event_lines)),
        _log_section(
            "Codex commands",
            redact_sensitive_output(codex_sections["command"] or _tail_text(codex_log, 180)),
        ),
        _log_section("QA commands", redact_sensitive_output(_tail_text(qa_log, 240))),
        _log_section("Docker runtime", redact_sensitive_output(_tail_text(docker_log, 180))),
        _log_section(
            "Deployment commands",
            redact_sensitive_output(_tail_text(deployment_log, 240)),
        ),
        _log_section("Codex raw events", raw_codex),
    ]
    raw_text = "\n\n".join(section for section in raw_sections if section.strip())

    st.caption("One live stream for Codex commentary, QA, deployment, and handoff progress.")
    _render_scrollable_markdown(
        "\n\n".join(friendly_entries) or "_No live activity yet._",
        height=360,
    )
    if codex_sections["diff"]:
        with st.expander("Diff / file changes"):
            st.code(codex_sections["diff"], language="diff")
    with st.expander("Developer raw logs", expanded=False):
        _render_scrollable_text(
            raw_text or "No raw logs are available yet.",
            height=420,
        )


def _render_scrollable_markdown(text: str, *, height: int) -> None:
    with st.container(height=height, border=True, autoscroll=True):
        st.markdown(text)


def _render_scrollable_text(text: str, *, height: int) -> None:
    with st.container(height=height, border=True, autoscroll=True):
        st.code(text, language="text")


def _log_section(title: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    return f"## {title}\n{body}"


def _tail_text(path: Path, max_lines: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-max_lines:])


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []

    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
