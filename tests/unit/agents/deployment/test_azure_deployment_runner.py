import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.deployment import AzureDeploymentRunner, write_deployment_request
from agentic_company.agents.deployment.runner import (
    _resolve_command,
    render_deployment_summary,
)
from agentic_company.agents.planning import run_pipeline


def test_azure_deployment_runner_deploys_generated_project(tmp_path):
    run_dir = _create_run(tmp_path)
    target_dir = run_dir / "generated-project"
    _write_generated_project(target_dir)
    write_deployment_request(run_dir, target_dir)
    executor = RecordingExecutor()

    result = AzureDeploymentRunner(command_executor=executor).run(run_dir)

    summary = (run_dir / "13-deployment-summary.md").read_text(encoding="utf-8")
    events = _read_events(run_dir)

    assert result.status == "deployment_deployed"
    assert "13-deployment-summary.md" in result.output_artifacts
    assert "09-handoff-summary.md" in result.output_artifacts
    assert "Status: deployed" in summary
    assert "https://simple-chat.example.azurecontainerapps.io" in summary
    assert "Post-deployment chatbot QA" in summary
    assert "az group delete --name" in summary
    assert (run_dir / "09-handoff-summary.md").exists()
    assert ["docker", "build", "-t"] == executor.command_prefix("Build container image")
    assert executor.command_prefix("Post-deployment chatbot QA") == [
        "uv",
        "run",
        "--with",
        "playwright",
    ]
    assert any(event["event"] == "deployment_started" for event in events)
    assert any(event["event"] == "deployment_completed" for event in events)
    assert any(event["event"] == "handoff_ready" for event in events)


def test_azure_deployment_runner_blocks_when_env_is_missing(tmp_path):
    run_dir = _create_run(tmp_path)
    target_dir = run_dir / "generated-project"
    _write_generated_project(target_dir, env_text="DEFAULT_MODEL=gpt-4o-mini\n")
    write_deployment_request(run_dir, target_dir)

    result = AzureDeploymentRunner(command_executor=RecordingExecutor()).run(run_dir)

    summary = (run_dir / "13-deployment-summary.md").read_text(encoding="utf-8")

    assert result.status == "deployment_blocked"
    assert "Missing required .env values: OPENAI_API_KEY" in summary


def test_deployment_summary_includes_failed_command_output():
    summary = render_deployment_summary(
        {
            "status": "blocked",
            "target_project_dir": "generated-project",
            "steps": [
                {
                    "name": "Azure account",
                    "status": "failed",
                    "details": "Command failed. az was not found on PATH.",
                    "output": "az was not found on PATH.",
                }
            ],
        }
    )

    assert "Status: blocked" in summary
    assert "Command failed. az was not found on PATH." in summary
    assert "## Failure Output" in summary
    assert "az was not found on PATH." in summary


def test_resolve_command_uses_az_cmd_on_windows(monkeypatch):
    monkeypatch.setattr("agentic_company.agents.deployment.runner.os.name", "nt")

    def fake_which(name):
        if name == "az.cmd":
            return r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
        return None

    monkeypatch.setattr("agentic_company.agents.deployment.runner.shutil.which", fake_which)

    resolved = _resolve_command(["az", "account", "show"])

    assert resolved == [
        r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        "account",
        "show",
    ]


class RecordingExecutor:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        if command_tuple == ("az", "account", "show"):
            return _completed(
                command,
                stdout=json.dumps(
                    {
                        "id": "subscription-id",
                        "name": "Pay-As-You-Go",
                        "tenantId": "tenant-id",
                        "user": {"name": "user@example.com"},
                    }
                ),
            )
        if command_tuple[:4] == ("az", "acr", "credential", "show"):
            return _completed(
                command,
                stdout=json.dumps(
                    {
                        "username": "registry-user",
                        "passwords": [{"name": "password", "value": "registry-password"}],
                    }
                ),
            )
        if command_tuple[:3] == ("az", "containerapp", "show"):
            if "--query" in command_tuple:
                return _completed(command, stdout="simple-chat.example.azurecontainerapps.io\n")
            return _completed(command, returncode=1, stderr="not found")
        return _completed(command)

    def command_prefix(self, step_name: str) -> list[str]:
        prefixes = {
            "Build container image": ["docker", "build", "-t"],
            "Post-deployment chatbot QA": ["uv", "run", "--with", "playwright"],
        }
        prefix = prefixes[step_name]
        for command in self.commands:
            if list(command[: len(prefix)]) == prefix:
                return list(command[: len(prefix)])
        return []


def _create_run(tmp_path: Path) -> Path:
    requirements = tmp_path / "requirements.md"
    requirements.write_text(
        """# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Required configuration:
- OPENAI_API_KEY
- DEFAULT_MODEL
""",
        encoding="utf-8",
    )
    return run_pipeline(requirements, tmp_path / "runs", run_id="azure-deployment-test")


def _write_generated_project(
    target_dir: Path,
    *,
    env_text: str = "OPENAI_API_KEY=sk-testvalue\nDEFAULT_MODEL=gpt-4o-mini\n",
) -> None:
    target_dir.mkdir(parents=True)
    (target_dir / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (target_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (target_dir / ".env.example").write_text(
        "OPENAI_API_KEY=\nDEFAULT_MODEL=gpt-4o-mini\n",
        encoding="utf-8",
    )
    (target_dir / ".env").write_text(env_text, encoding="utf-8")
    (target_dir / "README.md").write_text("# App\n", encoding="utf-8")


def _completed(
    command: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "ok",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _read_events(run_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
