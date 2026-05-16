"""Live log rendering for the Streamlit operator console."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentic_company.console.live_logs import friendly_log_entries
from agentic_company.console.support import read_events
from agentic_company.integrations.codex.events import (
    parse_codex_event_sections,
    render_raw_codex_events,
)
from agentic_company.platform.security import redact_sensitive_output


def render_live_logs(run_dir: Path) -> None:
    """Render one friendly log stream plus collapsible raw developer evidence."""

    st = _streamlit()
    events = read_events(run_dir)
    event_lines = [
        f"{event.get('timestamp', '')} {event.get('agent_id', '')} {event.get('event', '')}"
        for event in events
    ]
    codex_events = _read_codex_events(run_dir)
    codex_sections = parse_codex_event_sections(codex_events)
    raw_codex = (
        redact_sensitive_output(render_raw_codex_events(codex_events)) if codex_events else ""
    )
    qa_log = run_dir / "qa" / "commands.log"
    docker_log = run_dir / "qa" / "docker" / "runtime-command.log"
    deployment_log = run_dir / "deployment" / "commands.log"
    codex_log_text = _tail_codex_logs(run_dir, 180)
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
            redact_sensitive_output(codex_sections["command"] or codex_log_text),
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
    st = _streamlit()
    with st.container(height=height, border=True, autoscroll=True):
        st.markdown(text)


def _render_scrollable_text(text: str, *, height: int) -> None:
    st = _streamlit()
    with st.container(height=height, border=True, autoscroll=True):
        st.code(text, language="text")


def _streamlit():
    import streamlit as st

    return st


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


def _tail_codex_logs(run_dir: Path, max_lines: int) -> str:
    parts: list[str] = []
    for path in _codex_log_paths(run_dir):
        tail = _tail_text(path, max_lines)
        if tail:
            parts.append(f"## {path.relative_to(run_dir)}\n{tail}")
    return "\n\n".join(parts)


def _read_codex_events(run_dir: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for path in _codex_event_paths(run_dir):
        feature_id = _codex_feature_id(run_dir, path)
        agent_id = _codex_agent_id(run_dir, path)
        for event in _read_jsonl(path):
            event_agent_id = _codex_agent_id_from_event(event) or agent_id
            if event_agent_id:
                event = {**event, "agent_id": event_agent_id}
            if feature_id:
                event = {**event, "feature_id": feature_id}
            events.append(event)
    return events


def _codex_event_paths(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    codex_dir = run_dir / "codex"
    if codex_dir.exists():
        paths.extend(sorted(codex_dir.rglob("events.jsonl")))
    for upstream_codex_dir in _upstream_planning_codex_roots(run_dir):
        paths.extend(sorted(upstream_codex_dir.rglob("events.jsonl")))
    qa_codex_dir = run_dir / "qa" / "codex"
    if qa_codex_dir.exists():
        paths.extend(sorted(qa_codex_dir.rglob("events.jsonl")))
    deployment_codex_dir = run_dir / "deployment" / "codex"
    if deployment_codex_dir.exists():
        paths.extend(sorted(deployment_codex_dir.rglob("events.jsonl")))
    handoff_codex_dir = run_dir / "handoff" / "codex"
    if handoff_codex_dir.exists():
        paths.extend(sorted(handoff_codex_dir.rglob("events.jsonl")))
    team_lead_codex_dir = run_dir / "team-lead" / "codex-review"
    if team_lead_codex_dir.exists():
        paths.extend(sorted(team_lead_codex_dir.rglob("events.jsonl")))
    return sorted({path for path in paths if path.exists()})


def _codex_log_paths(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    codex_dir = run_dir / "codex"
    if codex_dir.exists():
        paths.extend(sorted(codex_dir.rglob("execution.log")))
    for upstream_codex_dir in _upstream_planning_codex_roots(run_dir):
        paths.extend(sorted(upstream_codex_dir.rglob("execution.log")))
    qa_codex_dir = run_dir / "qa" / "codex"
    if qa_codex_dir.exists():
        paths.extend(sorted(qa_codex_dir.rglob("execution.log")))
    deployment_codex_dir = run_dir / "deployment" / "codex"
    if deployment_codex_dir.exists():
        paths.extend(sorted(deployment_codex_dir.rglob("execution.log")))
    handoff_codex_dir = run_dir / "handoff" / "codex"
    if handoff_codex_dir.exists():
        paths.extend(sorted(handoff_codex_dir.rglob("execution.log")))
    team_lead_codex_dir = run_dir / "team-lead" / "codex-review"
    if team_lead_codex_dir.exists():
        paths.extend(sorted(team_lead_codex_dir.rglob("execution.log")))
    return sorted({path for path in paths if path.exists()})


def _codex_feature_id(run_dir: Path, path: Path) -> str:
    try:
        path.relative_to(run_dir / "team-lead" / "codex-review")
    except ValueError:
        pass
    else:
        return ""
    try:
        _relative_to_upstream_planning_codex_root(run_dir, path)
    except ValueError:
        pass
    else:
        return ""

    relative = _relative_codex_event_path(run_dir, path)
    if len(relative.parts) >= 2:
        return relative.parts[0]
    return ""


def _codex_agent_id(run_dir: Path, path: Path) -> str:
    try:
        path.relative_to(run_dir / "qa" / "codex")
    except ValueError:
        try:
            _relative_to_upstream_planning_codex_root(run_dir, path)
        except ValueError:
            try:
                path.relative_to(run_dir / "deployment" / "codex")
            except ValueError:
                try:
                    path.relative_to(run_dir / "handoff" / "codex")
                except ValueError:
                    try:
                        path.relative_to(run_dir / "team-lead" / "codex-review")
                    except ValueError:
                        return ""
                    return "team-lead-codex-review"
                return "handoff-codex-agent"
            return "deployment-codex-agent"
        return _upstream_planning_codex_agent_id(run_dir, path)
    return "qa-codex-agent"


def _upstream_planning_codex_agent_id(run_dir: Path, path: Path) -> str:
    try:
        relative = _relative_to_upstream_planning_codex_root(run_dir, path)
    except ValueError:
        return ""
    detected = _agent_id_from_parts([*path.parts, *relative.parts])
    if detected:
        return detected
    log_path = path.with_name("execution.log")
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        detected = _agent_id_from_text(log_text)
        if detected:
            return detected
    return ""


def _upstream_planning_codex_roots(run_dir: Path) -> list[Path]:
    upstream_dir = run_dir / "upstream-planning"
    roots = [upstream_dir / "codex"]
    if upstream_dir.exists():
        roots.extend(path / "codex" for path in upstream_dir.iterdir() if path.is_dir())
    return [path for path in roots if path.exists()]


def _relative_to_upstream_planning_codex_root(run_dir: Path, path: Path) -> Path:
    for root in _upstream_planning_codex_roots(run_dir):
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    raise ValueError


def _codex_agent_id_from_event(event: dict[str, object]) -> str:
    execution_id = str(event.get("codex_execution_id") or "")
    return _agent_id_from_text(execution_id)


def _agent_id_from_parts(parts: list[str]) -> str:
    aliases = _agent_aliases()
    for part in parts:
        normalized = part.lower()
        for alias, agent_id in aliases.items():
            if alias == normalized or agent_id == normalized or agent_id in normalized:
                return agent_id
    return ""


def _agent_id_from_text(text: str) -> str:
    normalized = text.lower()
    for match in re.findall(r"agent_id=([a-z0-9-]+)", normalized):
        if match in _known_agent_ids():
            return match
    for alias, agent_id in _agent_aliases().items():
        if agent_id in normalized or alias in normalized:
            return agent_id
    return ""


def _agent_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for agent_id in _known_agent_ids():
        aliases[agent_id] = agent_id
        if agent_id.endswith("-agent"):
            aliases[agent_id.removesuffix("-agent")] = agent_id
    return aliases


def _known_agent_ids() -> set[str]:
    from agentic_company.agents.registry import active_agents

    return {descriptor.agent_id for descriptor in active_agents()}


def _relative_codex_event_path(run_dir: Path, path: Path) -> Path:
    for root in [
        run_dir / "codex",
        run_dir / "qa" / "codex",
        run_dir / "deployment" / "codex",
        run_dir / "handoff" / "codex",
        run_dir / "team-lead" / "codex-review",
    ]:
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    try:
        return _relative_to_upstream_planning_codex_root(run_dir, path)
    except ValueError:
        pass
    return Path()


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
