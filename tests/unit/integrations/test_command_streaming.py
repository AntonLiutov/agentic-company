import sys
from pathlib import Path

import agentic_company.integrations.commands as commands
from agentic_company.integrations.commands import (
    StreamedCommand,
    append_completed_command_log,
    stream_command,
)


def test_stream_command_writes_live_log_and_returns_output(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    result = stream_command(
        StreamedCommand(
            command=[
                sys.executable,
                "-c",
                "print('hello from stream')",
            ],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            heading="Smoke",
        )
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert "hello from stream" in result.stdout
    assert "## Smoke" in log_text
    assert "status=running" in log_text
    assert "hello from stream" in log_text
    assert "exit_code=0" in log_text


def test_stream_command_redacts_log_but_returns_real_output(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    result = stream_command(
        StreamedCommand(
            command=[
                sys.executable,
                "-c",
                "print('OPENAI_API_KEY=sk-secretsecretsecret')",
            ],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            redactor=lambda value: value.replace("sk-secretsecretsecret", "***REDACTED***"),
        )
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert "sk-secretsecretsecret" in result.stdout
    assert "sk-secretsecretsecret" not in log_text
    assert "***REDACTED***" in log_text


def test_stream_command_passes_explicit_environment(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    result = stream_command(
        StreamedCommand(
            command=[
                sys.executable,
                "-c",
                "import os; print(os.environ['AGENTIC_TEST_ENV'])",
            ],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            env={"AGENTIC_TEST_ENV": "visible-to-child"},
        )
    )

    assert result.returncode == 0
    assert "visible-to-child" in result.stdout


def test_stream_command_handles_closed_stdin_without_crashing(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    result = stream_command(
        StreamedCommand(
            command=[sys.executable, "-c", "import sys; sys.exit(7)"],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            input_text="x" * 1_000_000,
        )
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert result.returncode == 7
    assert "exit_code=7" in log_text


def test_stream_command_returns_after_terminal_output_when_process_hangs(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    result = stream_command(
        StreamedCommand(
            command=[
                sys.executable,
                "-c",
                ('import time; print(\'{"type":"turn.completed"}\', flush=True); time.sleep(30)'),
            ],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            terminal_output_predicate=lambda line: "turn.completed" in line,
            terminal_grace_seconds=0.1,
        )
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert "turn.completed" in result.stdout
    assert "Terminal output was observed" in log_text
    assert "exit_code=0" in log_text


def test_stream_command_terminates_when_stop_requested(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "commands.log"
    terminated = {"called": False}
    original_terminate = commands._terminate_process

    def terminate(process):
        terminated["called"] = True
        original_terminate(process)

    monkeypatch.setattr(commands, "_terminate_process", terminate)

    result = stream_command(
        StreamedCommand(
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
            ],
            cwd=tmp_path,
            timeout_seconds=10,
            log_path=log_path,
            stop_requested=lambda: True,
        )
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert terminated["called"] is True
    assert result.returncode == 130
    assert result.stderr == "stop_requested"
    assert "Stop requested; terminating command." in log_text
    assert "exit_code=130" in log_text


def test_append_completed_command_log_supports_injected_executors(tmp_path: Path):
    log_path = tmp_path / "commands.log"

    append_completed_command_log(
        log_path=log_path,
        command=["actual", "secret"],
        display_command=["actual", "<redacted>"],
        cwd=tmp_path,
        exit_code=0,
        output="done",
        heading="Injected",
        status="passed",
        details="Command completed successfully.",
    )

    log_text = log_path.read_text(encoding="utf-8")

    assert "## Injected" in log_text
    assert "$ actual <redacted>" in log_text
    assert "status=passed" in log_text
    assert "details=Command completed successfully." in log_text
    assert "done" in log_text
