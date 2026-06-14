"""Shared live command streaming primitives for tool integrations."""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path

Redactor = Callable[[str], str]
LineCallback = Callable[[str], None]
LinePredicate = Callable[[str], bool]
StopPredicate = Callable[[], bool]


@dataclass(slots=True)
class StreamedCommand:
    """Command metadata used for user-facing and developer log output."""

    command: Sequence[str]
    cwd: Path
    timeout_seconds: int
    log_path: Path | None = None
    heading: str | None = None
    display_command: Sequence[str] | None = None
    input_text: str | None = None
    sensitive_output: bool = False
    redactor: Redactor | None = None
    on_stdout_line: LineCallback | None = None
    terminal_output_predicate: LinePredicate | None = None
    terminal_grace_seconds: float = 5.0
    stop_requested: StopPredicate | None = None
    env: Mapping[str, str | PathLike[str]] | None = None


def stream_command(spec: StreamedCommand) -> subprocess.CompletedProcess[str]:
    """Run a command while appending stdout/stderr to a log file as it arrives."""

    command = list(spec.command)
    output_parts: list[str] = []
    started_at = time.monotonic()
    log = _CommandLog(spec)
    log.write_start()

    try:
        process = subprocess.Popen(
            command,
            cwd=spec.cwd,
            stdin=subprocess.PIPE if spec.input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={str(key): str(value) for key, value in spec.env.items()}
            if spec.env is not None
            else None,
            **_process_group_kwargs(),
        )
    except FileNotFoundError as exc:
        message = f"{exc.filename or command[0]} was not found on PATH."
        log.write_output(message)
        log.write_exit(127)
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=message)

    if process.stdin and spec.input_text is not None:
        try:
            process.stdin.write(spec.input_text)
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            message = f"Process closed stdin before input could be written: {exc}"
            output_parts.append(message + "\n")
            log.write_output(message)
            try:
                process.stdin.close()
            except OSError:
                pass

    output_queue: queue.Queue[str | None] = queue.Queue()
    terminal_seen_at: float | None = None

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line)
        output_queue.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    while True:
        try:
            line = output_queue.get(timeout=0.2)
        except queue.Empty:
            line = ""

        if line is None:
            break
        if line:
            output_parts.append(line)
            log.write_output(line.rstrip())
            if spec.on_stdout_line:
                spec.on_stdout_line(line)
            if spec.terminal_output_predicate and spec.terminal_output_predicate(line):
                terminal_seen_at = time.monotonic()

        if (
            terminal_seen_at is not None
            and process.poll() is None
            and time.monotonic() - terminal_seen_at > spec.terminal_grace_seconds
        ):
            reason = (
                "Terminal output was observed, but the process did not exit before "
                f"the {spec.terminal_grace_seconds:g}s grace period elapsed."
            )
            output_parts.append(reason + "\n")
            log.write_output(reason)
            _terminate_process(process)
            log.write_exit(0)
            return subprocess.CompletedProcess(command, 0, stdout="".join(output_parts), stderr="")

        if spec.stop_requested and process.poll() is None and spec.stop_requested():
            reason = "Stop requested; terminating command."
            output_parts.append(reason + "\n")
            log.write_output(reason)
            _terminate_process(process)
            log.write_exit(130)
            return subprocess.CompletedProcess(
                command,
                130,
                stdout="".join(output_parts),
                stderr="stop_requested",
            )

        if time.monotonic() - started_at > spec.timeout_seconds:
            _kill_process(process)
            reason = f"Command timed out after {spec.timeout_seconds} seconds."
            output_parts.append(reason + "\n")
            log.write_output(reason)
            log.write_exit(124)
            return subprocess.CompletedProcess(
                command, 124, stdout="".join(output_parts), stderr=""
            )

    return_code = process.wait(timeout=5)
    log.write_exit(return_code)
    return subprocess.CompletedProcess(
        command, return_code, stdout="".join(output_parts), stderr=""
    )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    _signal_process_tree(process, force=False)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_process(process)


def _kill_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    _signal_process_tree(process, force=True)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return


def _process_group_kwargs() -> dict[str, bool]:
    if os.name == "nt":
        return {}
    return {"start_new_session": True}


def _signal_process_tree(process: subprocess.Popen[str], *, force: bool) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    signal_number = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(os.getpgid(process.pid), signal_number)
    except ProcessLookupError:
        return
    except PermissionError:
        if force:
            process.kill()
        else:
            process.terminate()


def append_completed_command_log(
    *,
    log_path: Path,
    command: Sequence[str],
    cwd: Path,
    exit_code: int | None,
    output: str,
    heading: str | None = None,
    display_command: Sequence[str] | None = None,
    status: str | None = None,
    details: str | None = None,
    redactor: Redactor | None = None,
) -> None:
    """Append a non-streamed command result, usually from an injected test executor."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        if heading:
            handle.write(f"## {heading}\n")
        command_text = " ".join(display_command or command)
        if redactor:
            command_text = redactor(command_text)
        handle.write(f"$ {command_text}\n")
        handle.write(f"cwd={cwd}\n")
        if status:
            handle.write(f"status={status}\n")
        handle.write(f"exit_code={exit_code}\n")
        if details:
            handle.write(f"details={details}\n")
        if output:
            safe_output = redactor(output.rstrip()) if redactor else output.rstrip()
            handle.write(safe_output + "\n")
        handle.write("\n")


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class _CommandLog:
    def __init__(self, spec: StreamedCommand) -> None:
        self.spec = spec

    def write_start(self) -> None:
        with self._open() as handle:
            if self.spec.heading:
                handle.write(f"## {self.spec.heading}\n")
            command_text = " ".join(self.spec.display_command or self.spec.command)
            if self.spec.redactor:
                command_text = self.spec.redactor(command_text)
            handle.write(f"$ {command_text}\n")
            handle.write(f"cwd={self.spec.cwd}\n")
            handle.write(f"started_at={timestamp_now()}\n")
            handle.write("status=running\n")
            handle.write("output:\n")
            handle.flush()

    def write_output(self, line: str) -> None:
        with self._open() as handle:
            if self.spec.sensitive_output:
                handle.write("[output redacted]\n")
            else:
                safe_line = self.spec.redactor(line) if self.spec.redactor else line
                handle.write(safe_line + "\n")
            handle.flush()

    def write_exit(self, exit_code: int) -> None:
        with self._open() as handle:
            handle.write(f"exit_code={exit_code}\n")
            handle.write(f"completed_at={timestamp_now()}\n\n")
            handle.flush()

    def _open(self) -> _NullWriter | object:
        if not self.spec.log_path:
            return _NullWriter()
        self.spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        return self.spec.log_path.open("a", encoding="utf-8")


class _NullWriter:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None

    def __enter__(self) -> _NullWriter:
        return self

    def __exit__(self, *_args: object) -> None:
        return None
