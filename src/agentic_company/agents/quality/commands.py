"""Command execution helpers for the QA agent."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.quality.models import CommandExecutor, QualityCheckResult
from agentic_company.integrations.commands import (
    StreamedCommand,
    append_completed_command_log,
    stream_command,
)
from agentic_company.platform.security import redact_sensitive_output

LOGGER = logging.getLogger(__name__)


def run_command_check(
    name: str,
    command: list[str],
    cwd: Path,
    *,
    command_executor: CommandExecutor | None,
    timeout_seconds: int,
    commands_log_path: Path,
) -> QualityCheckResult:
    LOGGER.info("QA command started name=%s command=%s cwd=%s", name, command, cwd)
    try:
        if command_executor:
            completed = execute_command(command, cwd, timeout_seconds, command_executor)
        else:
            completed = stream_command(
                StreamedCommand(
                    command=command,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    log_path=commands_log_path,
                    heading=name,
                    redactor=redact_sensitive_output,
                )
            )
    except subprocess.TimeoutExpired as exc:
        output = redact_sensitive_output((exc.stdout or "") + (exc.stderr or ""))
        append_command_log(commands_log_path, command, cwd, None, output, heading=name)
        result = QualityCheckResult(
            name=name,
            status="failed",
            command=command,
            exit_code=None,
            details=f"Command timed out after {timeout_seconds} seconds.",
            output=output.strip(),
        )
        LOGGER.warning("QA command timed out name=%s timeout_seconds=%s", name, timeout_seconds)
        return result
    except FileNotFoundError:
        LOGGER.warning("QA command skipped missing_executable=%s name=%s", command[0], name)
        return skipped_check(name, f"`{command[0]}` is not available on PATH.", command)

    output = redact_sensitive_output(((completed.stdout or "") + (completed.stderr or "")).strip())
    if command_executor:
        append_command_log(
            commands_log_path,
            command,
            cwd,
            completed.returncode,
            output,
            heading=name,
            status="passed" if completed.returncode == 0 else "failed",
        )
    result = QualityCheckResult(
        name=name,
        status="passed" if completed.returncode == 0 else "failed",
        command=command,
        exit_code=completed.returncode,
        details="Command completed successfully."
        if completed.returncode == 0
        else "Command failed.",
        output=output,
    )
    LOGGER.info(
        "QA command completed name=%s status=%s exit_code=%s",
        name,
        result.status,
        result.exit_code,
    )
    return result


def execute_command(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
    command_executor: CommandExecutor | None,
) -> subprocess.CompletedProcess[str]:
    if command_executor:
        return command_executor(command, cwd, timeout_seconds)
    return subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def append_command_log(
    path: Path,
    command: Sequence[str],
    cwd: Path,
    exit_code: int | None,
    output: str,
    *,
    heading: str | None = None,
    status: str | None = None,
) -> None:
    append_completed_command_log(
        log_path=path,
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        output=output,
        heading=heading,
        status=status,
        redactor=redact_sensitive_output,
    )


def skipped_check(
    name: str,
    details: str,
    command: list[str] | None = None,
    output: str = "",
) -> QualityCheckResult:
    return QualityCheckResult(
        name=name,
        status="skipped",
        command=command or [],
        exit_code=None,
        details=details,
        output=output,
    )
