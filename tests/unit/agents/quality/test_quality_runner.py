import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.planning import run_pipeline
from agentic_company.agents.quality import QualityRunner
from agentic_company.agents.quality.docker_checks import (
    DEV_DOCKER_COMPOSE_PROJECT,
    _docker_runtime_script,
)
from agentic_company.agents.quality.docker_summary import summarize_docker_runtime_log


def test_qa_runner_executes_project_checks_and_writes_evidence(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    _write_generated_project(run_dir / "generated-project")
    calls: list[list[str]] = []
    _patch_tool_lookup(monkeypatch, lambda name: name)

    def fake_executor(
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = QualityRunner(command_executor=fake_executor).run(run_dir)

    results = json.loads((run_dir / "qa" / "results.json").read_text(encoding="utf-8"))
    test_plan = json.loads((run_dir / "qa" / "test-plan.json").read_text(encoding="utf-8"))
    report = (run_dir / "08-qa-report.md").read_text(encoding="utf-8")
    command_log = (run_dir / "qa" / "commands.log").read_text(encoding="utf-8")
    docker_summary = json.loads(
        (run_dir / "qa" / "docker" / "build-summary.json").read_text(encoding="utf-8")
    )

    assert result.status == "qa_passed"
    assert results["status"] == "passed"
    assert results["docker_build_summary"] == "qa/docker/build-summary.json"
    assert docker_summary["status"] == "not_available"
    assert ["uv", "sync", "--frozen"] in calls
    assert ["uv", "run", "python", "-m", "py_compile", "app.py"] in calls
    assert ["docker", "compose", "config"] in calls
    assert any(
        call[:5] == ["uv", "run", "--with", "playwright", "python"]
        and call[-1].endswith("docker_runtime_e2e.py")
        for call in calls
    )
    assert any(
        call[:5] == ["uv", "run", "--with", "playwright", "python"]
        and call[-1].endswith("playwright_live_chat_e2e.py")
        for call in calls
    )
    assert any(item["name"] == "Docker runtime E2E" for item in test_plan)
    assert any(item["name"] == "Playwright live chat E2E" for item in test_plan)
    assert "Streamlit AppTest" in report
    assert "Docker runtime E2E" in report
    assert "Playwright live chat E2E" in report
    assert "qa/browser/docker-chat-transcript.json" in report
    assert "qa/browser/chat-transcript.json" in report
    assert "qa/scripts/docker_runtime_e2e.py" in report
    assert "qa/scripts/playwright_live_chat_e2e.py" in report
    assert "Coverage Summary" in report
    assert "Docker Build Observability" in report
    assert "qa/docker/build-summary.json" in report
    assert "qa/results.json" in report
    assert "qa/test-plan.json" in report
    assert "$ uv sync --frozen" in command_log
    assert any(event["event"] == "qa_completed" for event in _read_events(run_dir))


def test_qa_runner_records_failures_and_ignores_local_env_secret(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    target_dir = run_dir / "generated-project"
    _write_generated_project(target_dir)
    (target_dir / "app.py").write_text(
        "OPENAI_API_KEY = 'sk-secretsecretsecret'\n", encoding="utf-8"
    )
    (target_dir / ".env").write_text("OPENAI_API_KEY=sk-real-local-secret\n", encoding="utf-8")
    _patch_tool_lookup(monkeypatch, lambda name: None)

    result = QualityRunner(command_executor=_passing_executor).run(run_dir)

    results = json.loads((run_dir / "qa" / "results.json").read_text(encoding="utf-8"))

    assert result.status == "qa_failed"
    assert results["status"] == "failed"
    assert any(
        check["name"] == "Secret scan" and check["status"] == "failed"
        for check in results["checks"]
    )


def test_qa_runner_is_idempotent_when_evidence_exists(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    _write_generated_project(run_dir / "generated-project")
    _patch_tool_lookup(monkeypatch, lambda name: None)
    runner = QualityRunner(command_executor=_passing_executor)

    first = runner.run(run_dir)
    before = (run_dir / "events.jsonl").read_text(encoding="utf-8")
    second = runner.run(run_dir)
    after = (run_dir / "events.jsonl").read_text(encoding="utf-8")

    assert first.status.startswith("qa_")
    assert second.status == "already_completed"
    assert after == before


def test_qa_runner_redacts_secrets_from_command_evidence(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    _write_generated_project(run_dir / "generated-project")
    _patch_tool_lookup(monkeypatch, lambda name: name)

    def leaking_executor(
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="OPENAI_API_KEY=sk-secretsecretsecret\nsafe line",
            stderr="",
        )

    QualityRunner(command_executor=leaking_executor).run(run_dir)

    results_text = (run_dir / "qa" / "results.json").read_text(encoding="utf-8")
    command_log = (run_dir / "qa" / "commands.log").read_text(encoding="utf-8")

    assert "sk-secretsecretsecret" not in results_text
    assert "sk-secretsecretsecret" not in command_log
    assert "***REDACTED***" in results_text
    assert "***REDACTED***" in command_log


def test_qa_runner_fails_live_browser_qa_when_run_env_is_missing(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    _write_generated_project(run_dir / "generated-project", include_env=False)
    _patch_tool_lookup(monkeypatch, lambda name: name)

    result = QualityRunner(command_executor=_passing_executor).run(run_dir)
    results = json.loads((run_dir / "qa" / "results.json").read_text(encoding="utf-8"))

    assert result.status == "qa_failed"
    assert any(
        check["name"] in {"Docker runtime E2E", "Playwright live chat E2E"}
        and check["status"] == "failed"
        and "OPENAI_API_KEY" in check["details"]
        for check in results["checks"]
    )


def test_docker_runtime_log_summary_extracts_slow_dependency_step(tmp_path):
    log_path = tmp_path / "runtime-command.log"
    log_path.write_text(
        """$ docker compose -p agentic_qa up --build -d
#7 [2/6] WORKDIR /app
#7 CACHED
#9 [4/6] RUN uv sync --frozen --no-dev
#9 100.1 Downloaded streamlit
#9 300.2 Downloaded pyarrow
#9 900.2 Prepared 49 packages in 14m 59s
#9 DONE 904.6s
#12 [internal] exporting to image
#12 DONE 34.9s
""",
        encoding="utf-8",
    )

    summary = summarize_docker_runtime_log(log_path)

    assert summary["status"] == "available"
    assert summary["dependency_sync_seconds"] == 904.6
    assert summary["cached_steps"] == 1
    assert summary["downloaded_packages"] == ["streamlit", "pyarrow"]
    assert summary["slowest_step"] == {
        "id": "#9",
        "label": "[4/6] RUN uv sync --frozen --no-dev",
        "seconds": 904.6,
    }
    assert any("dependency sync dominated" in item for item in summary["observations"])


def test_docker_runtime_script_uses_stable_dev_compose_project(tmp_path):
    script = _docker_runtime_script(tmp_path / "qa")

    assert f'compose_project = "{DEV_DOCKER_COMPOSE_PROJECT}"' in script
    assert "safe_project_name" not in script


def _create_planning_run(tmp_path: Path) -> Path:
    requirements = tmp_path / "requirements.md"
    requirements.write_text(
        """# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Target user:
A solo builder testing simple assistant ideas locally.

Core features:
- User can enter a message
- App sends the message to an LLM

Required configuration:
- OPENAI_API_KEY
- DEFAULT_MODEL

Preferred stack:
- Python
- Streamlit

Acceptance criteria:
- App starts locally with Streamlit
- Missing API key does not crash the app
""",
        encoding="utf-8",
    )
    return run_pipeline(requirements, tmp_path / "runs", run_id="qa-runner-test")


def _write_generated_project(target_dir: Path, *, include_env: bool = True) -> None:
    files = {
        ".env": "OPENAI_API_KEY=sk-test\nDEFAULT_MODEL=gpt-4o-mini\n",
        ".env.example": "OPENAI_API_KEY=\nDEFAULT_MODEL=gpt-4o-mini\n",
        ".streamlit/config.toml": '[theme]\nbase = "dark"\n',
        "Dockerfile": "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim\n",
        "README.md": (
            "# Simple LLM Chat\n\n"
            "## Setup\n\n"
            "Use `uv sync`, then run with Streamlit.\n"
            "Docker users can run `docker compose up --build`.\n"
            "Required env vars: `OPENAI_API_KEY` and `DEFAULT_MODEL`.\n"
        ),
        "app.py": "print('hello')\n",
        "docker-compose.yml": "services:\n  app:\n    build: .\n",
        "execution-summary.md": "# Execution Summary\n",
        "pyproject.toml": '[project]\nname = "simple-llm-chat"\n',
        "uv.lock": "",
    }
    if not include_env:
        files.pop(".env")
    for relative_path, content in files.items():
        path = target_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _passing_executor(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")


def _patch_tool_lookup(monkeypatch, lookup):
    monkeypatch.setattr("agentic_company.agents.quality.python_checks.shutil.which", lookup)
    monkeypatch.setattr("agentic_company.agents.quality.docker_checks.shutil.which", lookup)
    monkeypatch.setattr(
        "agentic_company.agents.quality.playwright_checks.shutil.which",
        lookup,
    )


def _read_events(run_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
