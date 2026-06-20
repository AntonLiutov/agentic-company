"""Execution identity helpers for agent and Codex runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from agentic_company.platform.artifacts.artifacts import read_text_artifact


def slugify(value: object, *, default_value: str = "run") -> str:
    """Return a compact filesystem-safe slug."""

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip()).strip("-").lower()
    return slug or default_value


def short_hash(value: object, *, length: int = 8) -> str:
    """Return a stable short hash for IDs that should remain readable."""

    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def build_agent_execution_id(
    *,
    run_id: str,
    agent_id: str,
    correlation_id: str = "",
    intent: str = "",
    message_id: str = "",
    attempt: int | None = None,
) -> str:
    """Build a platform-owned execution id for one agent/tool invocation."""

    parts = [
        "exec",
        slugify(run_id),
        slugify(agent_id),
        slugify(correlation_id, default_value="correlation"),
        slugify(intent, default_value="task"),
    ]
    if attempt is not None:
        parts.append(f"attempt-{attempt}")
    if message_id:
        parts.append(short_hash(message_id))
    return "-".join(parts)


def build_codex_execution_id(
    *,
    execution_id: str,
    codex_agent_id: str,
    attempt: int | None = None,
) -> str:
    """Build a Codex-specific execution id under a platform agent execution."""

    parts = ["codex", slugify(execution_id), slugify(codex_agent_id)]
    if attempt is not None:
        parts.append(f"attempt-{attempt}")
    return "-".join(parts)


def execution_artifact_dir_name(execution_id: str) -> str:
    """Return a short filesystem label for execution-scoped artifacts."""

    slug = slugify(execution_id, default_value="execution")
    suffix = slug.rsplit("-", maxsplit=1)[-1]
    if not re.fullmatch(r"[0-9a-f]{8}", suffix):
        suffix = short_hash(slug)
    return f"exec-{suffix}"


def execution_artifact_dir(*, root: Path, execution_id: str, attempt: int | None = None) -> Path:
    """Return an execution-scoped artifact directory."""

    path = root / execution_artifact_dir_name(execution_id)
    if attempt is not None:
        path = path / f"attempt-{attempt}"
    return path


def extract_codex_thread_id(events_path: Path) -> str:
    """Read a Codex thread id from a JSONL events artifact if present."""

    if not events_path.exists():
        return ""
    for line in read_text_artifact(events_path).splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        thread_id = event.get("thread_id") or params.get("threadId") or params.get("thread_id")
        if thread_id:
            return str(thread_id)
    return ""
