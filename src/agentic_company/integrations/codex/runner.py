"""Codex CLI process execution helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.integrations.codex.cli import resolve_codex_binary
from agentic_company.integrations.codex.events import append_raw_codex_event
from agentic_company.integrations.commands import StreamedCommand, stream_command


def build_codex_exec_command(
    *,
    codex_binary: str | None,
    model: str,
    sandbox: str,
    target_project_dir: str,
    run_dir: Path,
    summary_path: Path,
) -> list[str]:
    """Build the low-level `codex exec` command."""

    binary = codex_binary or resolve_codex_binary()
    return [
        binary,
        "exec",
        "--model",
        model,
        "--sandbox",
        sandbox,
        "--cd",
        target_project_dir,
        "--add-dir",
        str(run_dir),
        "--skip-git-repo-check",
        "--json",
        "--output-last-message",
        str(summary_path),
        "-",
    ]


def stream_codex_exec_to_log(
    command: Sequence[str],
    prompt: str,
    timeout_seconds: int,
    log_path: Path,
    raw_events_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Run Codex while streaming command output and raw JSON events to artifacts."""

    raw_events_path.write_text("", encoding="utf-8")
    log_path.write_text(f"timeout_seconds={timeout_seconds}\n\n", encoding="utf-8")
    return stream_command(
        StreamedCommand(
            command=command,
            cwd=log_path.parent,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
            input_text=prompt,
            on_stdout_line=lambda line: append_raw_codex_event(raw_events_path, line),
        )
    )
