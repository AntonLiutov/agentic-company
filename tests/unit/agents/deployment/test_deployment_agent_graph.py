import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentic_company.agents.deployment import AzureDeploymentRunner
from agentic_company.agents.deployment.graph import (
    DEPLOYMENT_AGENT_GRAPH_NODE_ORDER,
    build_deployment_agent_graph,
    render_deployment_agent_graph_mermaid,
    run_deployment_agent_graph,
)
from agentic_company.agents.planning import run_pipeline
from agentic_company.platform.state import initial_delivery_state


def test_deployment_agent_graph_runs_azure_workflow_and_maps_state(tmp_path):
    run_dir = _create_run(tmp_path)
    target_dir = run_dir / "generated-project"
    _write_generated_project(target_dir)
    runner = AzureDeploymentRunner(command_executor=RecordingExecutor())
    state = initial_delivery_state(run_id="run", run_dir=run_dir)

    result = run_deployment_agent_graph(state, runner)

    assert result["stage"] == "deployment"
    assert result["status"] == "deployment_deployed"
    assert result["deployment_status"] == "deployed"
    assert result["public_url"] == "https://simple-chat.example.azurecontainerapps.io"
    assert result["completed_nodes"] == ["deployment"]
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "11-deployment-plan.json",
        "11-deployment-plan.md",
        "12-deployment-request.json",
        "12-deployment-request.md",
        "13-deployment-summary.md",
        "deployment/commands.log",
    ]
    assert (run_dir / "11-deployment-plan.md").exists()
    assert (run_dir / "12-deployment-request.md").exists()
    assert (run_dir / "13-deployment-summary.md").exists()
    assert not (run_dir / "09-handoff-summary.md").exists()


def test_deployment_agent_graph_exposes_expected_node_order():
    assert DEPLOYMENT_AGENT_GRAPH_NODE_ORDER == [
        "prepare_context",
        "write_deployment_plan",
        "write_deployment_request",
        "load_deployment_request",
        "validate_environment",
        "check_azure_account",
        "check_docker",
        "select_subscription",
        "ensure_resource_group",
        "ensure_registry",
        "build_and_push_image",
        "read_registry_credentials",
        "ensure_container_environment",
        "create_or_update_container_app",
        "read_public_url",
        "run_post_deploy_qa",
        "write_summary",
        "apply_result",
    ]


def test_deployment_agent_graph_mermaid_includes_internal_nodes():
    mermaid = render_deployment_agent_graph_mermaid()

    assert "write_deployment_plan" in mermaid
    assert "write_deployment_request" in mermaid
    assert "load_deployment_request" in mermaid
    assert "check_azure_account" in mermaid
    assert "build_and_push_image" in mermaid
    assert "run_post_deploy_qa" in mermaid
    assert "write_summary" in mermaid


def test_deployment_agent_graph_requires_nodes():
    runner = AzureDeploymentRunner(command_executor=RecordingExecutor())

    with pytest.raises(ValueError, match="requires at least one node"):
        build_deployment_agent_graph(runner, node_order=[])


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
    return run_pipeline(requirements, tmp_path / "runs", run_id="deployment-graph-test")


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
