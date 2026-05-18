import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.fullstack import CodexCliRunner
from agentic_company.agents.fullstack.codex_cli import build_codex_prompt
from agentic_company.integrations.codex import DEFAULT_CODEX_SANDBOX
from agentic_company.integrations.codex.cli import resolve_codex_binary
from agentic_company.integrations.codex.events import (
    append_raw_codex_event,
    parse_codex_event_sections,
    render_raw_codex_events,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts import EXECUTION_REQUEST_ARTIFACT


def test_codex_cli_runner_invokes_codex_exec_with_planning_context(
    tmp_path, write_sample_requirements, monkeypatch
):
    monkeypatch.delenv("AGENTIC_CODEX_SERVICE_TIER", raising=False)
    run_dir = _create_planning_run(tmp_path, write_sample_requirements)
    calls: list[tuple[Sequence[str], str, int, Path]] = []

    def fake_executor(
        command: Sequence[str],
        prompt: str,
        timeout_seconds: int,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, prompt, timeout_seconds, log_path))
        summary_path = Path(command[command.index("--output-last-message") + 1])
        summary_path.write_text(
            "# Execution Summary\n\nStatus: codex completed\n", encoding="utf-8"
        )
        log_path.write_text("streamed log output\n", encoding="utf-8")
        stdout = "\n".join(
            [
                json.dumps({"method": "turn/started", "params": {}}),
                json.dumps(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {"delta": "Working now.", "phase": "commentary"},
                    }
                ),
                json.dumps(
                    {
                        "method": "item/commandExecution/outputDelta",
                        "params": {"delta": "pytest passed\n"},
                    }
                ),
                json.dumps(
                    {
                        "method": "turn/diff/updated",
                        "params": {"diff": "diff --git a/app.py b/app.py\n"},
                    }
                ),
                json.dumps(
                    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
                ),
            ]
        )
        raw_events_path.write_text(stdout + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = CodexCliRunner(
        codex_binary="codex-test",
        timeout_seconds=42,
        command_executor=fake_executor,
    ).run(run_dir)

    command, prompt, timeout_seconds, log_path = calls[0]

    assert result.status == "codex_completed"
    assert timeout_seconds == 42
    assert log_path == run_dir / "codex" / "execution.log"
    assert (run_dir / "codex" / "prompt.md").exists()
    assert (run_dir / "codex" / "execution.log").exists()
    assert not (run_dir / "08-qa-report.md").exists()
    assert not (run_dir / "09-handoff-summary.md").exists()
    assert not (run_dir / "12-deployment-request.md").exists()
    assert command[:2] == ["codex-test", "exec"]
    assert command[command.index("--model") + 1] == "gpt-5.3-codex"
    config_values = [
        command[index + 1] for index, value in enumerate(command) if value == "--config"
    ]
    assert 'model_reasoning_effort="medium"' in config_values
    assert 'service_tier="fast"' not in config_values
    assert "shell_environment_policy.inherit=all" in config_values
    assert command[command.index("--sandbox") + 1] == DEFAULT_CODEX_SANDBOX
    assert command[command.index("--cd") + 1] == str(run_dir / "generated-project")
    assert command[command.index("--add-dir") + 1] == str(run_dir)
    assert "--skip-git-repo-check" in command
    assert "--json" in command
    assert command[-1] == "-"
    assert "Request context:" in prompt
    assert "Simple LLM Chat" in prompt
    assert "Work only inside the target project directory." in prompt
    assert "Do not print secret values" in prompt
    assert "Prefer `uv`" in prompt
    assert "pyproject.toml" in prompt
    assert "uv.lock" in prompt
    assert "docker compose up --build" in prompt
    assert "Do not install `uv` with `pip` inside Docker" in prompt
    assert "--mount=type=cache,target=/root/.cache/uv" in prompt
    assert "uv run --no-sync" in prompt
    assert "dependency cache" in prompt
    events = _read_jsonl(run_dir / "codex" / "events.jsonl")
    sections = parse_codex_event_sections(events)
    assert "Turn started" in sections["events"]
    assert "Working now." in sections["commentary"]
    assert "pytest passed" in sections["command"]
    assert "diff --git" in sections["diff"]

    events = _read_events(run_dir)
    assert any(
        event["event"] == "execution_started" and event["data"]["provider"] == "codex-cli"
        for event in events
    )
    assert any(
        event["event"] == "execution_completed" and event["data"]["status"] == "codex_completed"
        for event in events
    )


def test_codex_cli_runner_does_not_skip_when_summary_exists(tmp_path, write_sample_requirements):
    run_dir = _create_planning_run(tmp_path, write_sample_requirements)
    (run_dir / "07-execution-summary.md").write_text("old summary", encoding="utf-8")
    calls = 0

    def fake_executor(
        command: Sequence[str],
        prompt: str,
        timeout_seconds: int,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        summary_path = Path(command[command.index("--output-last-message") + 1])
        summary_path.write_text("new summary", encoding="utf-8")
        raw_events_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            command, 0, stdout='{"type":"turn.completed"}', stderr=""
        )

    result = CodexCliRunner(codex_binary="codex-test", command_executor=fake_executor).run(run_dir)

    assert calls == 1
    assert result.status == "codex_completed"
    assert result.summary == "new summary"


def test_codex_cli_runner_resumes_existing_codex_thread(tmp_path, write_sample_requirements):
    run_dir = _create_planning_run(tmp_path, write_sample_requirements)
    request_path = run_dir / "delivery/execution-request.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["codex_resume_thread_id"] = "thread-existing"
    request_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    calls: list[Sequence[str]] = []

    def fake_executor(
        command: Sequence[str],
        prompt: str,
        timeout_seconds: int,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        summary_path = Path(command[command.index("--output-last-message") + 1])
        summary_path.write_text("resumed summary", encoding="utf-8")
        raw_events_path.write_text('{"type":"turn.completed"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(
            command, 0, stdout='{"type":"turn.completed"}', stderr=""
        )

    result = CodexCliRunner(codex_binary="codex-test", command_executor=fake_executor).run(run_dir)

    assert calls[0][-3:] == ["resume", "thread-existing", "-"]
    assert result.status == "codex_completed"
    assert result.codex_thread_id == "thread-existing"
    events = _read_events(run_dir)
    assert any(
        event["event"] == "execution_started"
        and event["data"]["codex_resume_thread_id"] == "thread-existing"
        for event in events
    )


def test_codex_prompt_scopes_active_feature(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "05-implementation-brief.md").write_text(
        "# Implementation Brief\n\nFeature Queue\n",
        encoding="utf-8",
    )
    request = _execution_request(
        run_dir,
        active_feature={
            "id": "F2",
            "title": "Mark tasks done",
            "acceptance_criteria": ["API can mark a task as done"],
            "delivery_order": 2,
        },
        completed_feature_ids=["F1"],
    )

    prompt = build_codex_prompt(request, run_dir)

    assert "Active feature: `F2` - Mark tasks done" in prompt
    assert "API can mark a task as done" in prompt
    assert "Completed features before this run: F1" in prompt
    assert "Implement only the active feature in this Codex run." in prompt
    assert "Preserve behavior for completed features" in prompt
    assert "service names must be exactly `api` and `web`" in prompt
    assert "agentic-{app-slug}-{service}:latest" in prompt
    assert "agentic-{app-slug}-{service}" in prompt
    assert "Workspace ownership:" in prompt
    assert "Product implementation files belong inside" in prompt
    assert "Do not write QA, deployment, handoff, or orchestration artifacts" in prompt
    assert "scripts/`, `tests/`, or a clearly" in prompt
    assert "project-local `.gitignore` and `.dockerignore`" in prompt
    assert "platform repository's root `.gitignore`" in prompt
    assert "Cloud/runtime readiness" in prompt
    assert "local Docker smoke checks" in prompt
    assert "configuration-driven\n  persistence" in prompt
    assert "Deployment or QA evidence showing an application runtime/cloud" in prompt


def test_codex_cli_runner_records_failure_summary_when_codex_fails(
    tmp_path, write_sample_requirements
):
    run_dir = _create_planning_run(tmp_path, write_sample_requirements)

    def fake_executor(
        command: Sequence[str],
        prompt: str,
        timeout_seconds: int,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        log_path.write_text("boom\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    result = CodexCliRunner(command_executor=fake_executor).run(run_dir)

    assert result.status == "codex_failed"
    assert (run_dir / "07-execution-summary.md").exists()
    assert "boom" in result.summary

    events = _read_events(run_dir)
    assert any(
        event["event"] == "execution_failed" and event["data"]["status"] == "codex_failed"
        for event in events
    )


def test_codex_cli_runner_can_retry_after_failed_summary(tmp_path, write_sample_requirements):
    run_dir = _create_planning_run(tmp_path, write_sample_requirements)
    calls = 0

    def failing_executor(
        command: Sequence[str],
        prompt: str,
        timeout_seconds: int,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        log_path.write_text("boom\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    runner = CodexCliRunner(command_executor=failing_executor)

    first = runner.run(run_dir)
    second = runner.run(run_dir)

    assert first.status == "codex_failed"
    assert second.status == "codex_failed"
    assert calls == 2


def test_resolve_codex_binary_prefers_explicit_env_override(tmp_path):
    npm_bin = tmp_path / "ops" / "codex-npm-smoke" / ".codex-npm" / "node_modules" / ".bin"
    npm_bin.mkdir(parents=True)
    codex = npm_bin / "codex.cmd"
    codex.write_text("", encoding="utf-8")

    resolved = resolve_codex_binary(
        env={"CODEX_BINARY": "C:\\custom\\codex.exe"},
        repo_root=tmp_path,
        path_lookup=lambda _name: "ignored",
    )

    assert resolved == "C:\\custom\\codex.exe"


def test_resolve_codex_binary_auto_prefers_repo_local_npm_codex(tmp_path):
    npm_bin = tmp_path / "ops" / "codex-npm-smoke" / ".codex-npm" / "node_modules" / ".bin"
    npm_bin.mkdir(parents=True)
    codex = npm_bin / "codex.cmd"
    codex.write_text("", encoding="utf-8")

    resolved = resolve_codex_binary(
        env={},
        repo_root=tmp_path,
        path_lookup=lambda _name: "ignored",
    )

    assert resolved == str(codex)


def test_resolve_codex_binary_uses_chatgpt_extension_when_mode_is_extension(tmp_path):
    extension_root = (
        tmp_path
        / ".vscode"
        / "extensions"
        / "openai.chatgpt-26.422.30944-win32-x64"
        / "bin"
        / "windows-x86_64"
    )
    extension_root.mkdir(parents=True)
    codex = extension_root / "codex.exe"
    codex.write_text("", encoding="utf-8")

    npm_bin = tmp_path / "ops" / "codex-npm-smoke" / ".codex-npm" / "node_modules" / ".bin"
    npm_bin.mkdir(parents=True)
    (npm_bin / "codex.cmd").write_text("", encoding="utf-8")

    resolved = resolve_codex_binary(
        env={"AGENTIC_CODEX_BINARY_MODE": "extension"},
        repo_root=tmp_path,
        home=tmp_path,
        path_lookup=lambda _name: None,
    )

    assert resolved == str(codex)


def test_resolve_codex_binary_uses_chatgpt_extension_when_legacy_flag_is_allowed(tmp_path):
    extension_root = (
        tmp_path
        / ".vscode"
        / "extensions"
        / "openai.chatgpt-26.422.30944-win32-x64"
        / "bin"
        / "windows-x86_64"
    )
    extension_root.mkdir(parents=True)
    codex = extension_root / "codex.exe"
    codex.write_text("", encoding="utf-8")

    resolved = resolve_codex_binary(
        env={"AGENTIC_CODEX_ALLOW_EXTENSION_BINARY": "1"},
        repo_root=tmp_path,
        home=tmp_path,
        path_lookup=lambda _name: None,
    )

    assert resolved == str(codex)


def test_resolve_codex_binary_rejects_invalid_mode(tmp_path):
    try:
        resolve_codex_binary(
            env={"AGENTIC_CODEX_BINARY_MODE": "spaceship"},
            repo_root=tmp_path,
            home=tmp_path,
            path_lookup=lambda _name: None,
        )
    except ValueError as exc:
        assert "AGENTIC_CODEX_BINARY_MODE must be one of" in str(exc)
    else:
        raise AssertionError("Expected invalid Codex binary mode to fail fast.")


def test_resolve_codex_binary_skips_chatgpt_extension_by_default(tmp_path):
    extension_root = (
        tmp_path
        / ".vscode"
        / "extensions"
        / "openai.chatgpt-26.422.30944-win32-x64"
        / "bin"
        / "windows-x86_64"
    )
    extension_root.mkdir(parents=True)
    (extension_root / "codex.exe").write_text("", encoding="utf-8")

    resolved = resolve_codex_binary(
        env={},
        repo_root=tmp_path,
        home=tmp_path,
        path_lookup=lambda _name: None,
    )

    assert resolved == "codex"


def test_parse_codex_event_sections_handles_completed_agent_messages_and_file_changes():
    events = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "agent_message",
                "text": "I\u00e2\u20ac\u2122ll inspect first.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "agent_message",
                "text": "Then I\u00e2\u20ac\u2122ll write files.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "python -m py_compile app.py",
                "aggregated_output": "",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "file_change",
                "status": "completed",
                "changes": [{"kind": "add", "path": "app.py"}],
            },
        },
    ]

    sections = parse_codex_event_sections(events)
    raw = render_raw_codex_events(events)

    assert "Thread started: thread-1" in sections["events"]
    assert "I\u2019ll inspect first.\n\nThen I\u2019ll write files." in sections["commentary"]
    assert "python -m py_compile app.py" in sections["command"]
    assert "exit_code=0" in sections["command"]
    assert "- add: app.py" in sections["diff"]
    assert "I\u2019ll inspect first." in raw


def test_append_raw_codex_event_persists_json_events_as_lines(tmp_path):
    raw_events_path = tmp_path / "codex-events.jsonl"

    append_raw_codex_event(
        raw_events_path,
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "I\u00e2\u20ac\u2122m live."},
            }
        ),
    )
    append_raw_codex_event(raw_events_path, "plain stderr line")

    events = _read_jsonl(raw_events_path)
    sections = parse_codex_event_sections(events)

    assert len(events) == 1
    assert "I\u2019m live." in sections["commentary"]


def test_write_structured_codex_artifacts_preserves_streamed_timestamps(tmp_path):
    run_dir = tmp_path / "run"
    raw_path = run_dir / "codex" / "events.jsonl"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "recorded_at": "2026-04-27T00:36:01",
                "item": {"type": "agent_message", "text": "streamed first"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts = write_structured_codex_artifacts(
        run_dir,
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "final rewrite should not replace streamed file",
                },
            }
        ),
        raw_events_filename="codex/events.jsonl",
    )

    events = _read_jsonl(raw_path)

    assert artifacts == ["codex/events.jsonl"]
    assert events[0]["recorded_at"] == "2026-04-27T00:36:01"
    assert events[0]["item"]["text"] == "streamed first"


def test_append_raw_codex_event_redacts_secret_values(tmp_path):
    raw_path = tmp_path / "events.jsonl"

    append_raw_codex_event(
        raw_path,
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "aggregated_output": (
                        "OPENAI_API_KEY=sk-secretsecretsecret\nCODEX_API_KEY=sk-codexsecretsecret"
                    ),
                },
            }
        ),
    )

    raw_text = raw_path.read_text(encoding="utf-8")

    assert "sk-secretsecretsecret" not in raw_text
    assert "sk-codexsecretsecret" not in raw_text
    assert "***REDACTED***" in raw_text


def _create_planning_run(tmp_path: Path, write_sample_requirements) -> Path:
    run_dir = tmp_path / "runs" / "codex-runner-test"
    run_dir.mkdir(parents=True)
    requirements = run_dir / "00-requirements.md"
    write_sample_requirements(requirements)
    (run_dir / "05-implementation-brief.md").write_text(
        "# Implementation Brief\n\nSimple LLM Chat\n", encoding="utf-8"
    )
    request = _execution_request(run_dir)
    request_path = run_dir / EXECUTION_REQUEST_ARTIFACT
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(json.dumps(request.to_dict(), indent=2) + "\n", encoding="utf-8")
    return run_dir


def _execution_request(
    run_dir: Path,
    *,
    active_feature: dict[str, object] | None = None,
    completed_feature_ids: list[str] | None = None,
):
    from agentic_company.platform.models import ExecutionRequest

    return ExecutionRequest(
        run_id=run_dir.name,
        agent_id="fullstack-agent",
        agent_version="0.1.0",
        maturity_level="L6 Codex Agent",
        provider="codex",
        model="gpt-5.3-codex",
        target_project_dir=str(run_dir / "generated-project"),
        input_artifacts=["05-implementation-brief.md"],
        expected_outputs=["api/app.py"],
        instructions=["Build the active feature."],
        constraints=["Keep names stable."],
        feature_queue=[active_feature] if active_feature else [],
        active_feature=active_feature,
        completed_feature_ids=completed_feature_ids or [],
    )


def _read_events(run_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
