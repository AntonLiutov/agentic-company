import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentic_company.agents.planning import run_pipeline
from agentic_company.agents.quality.graph import (
    QUALITY_AGENT_GRAPH_NODE_ORDER,
    build_quality_agent_graph,
    render_quality_agent_graph_mermaid,
    run_quality_agent_graph,
)
from agentic_company.platform.state import initial_delivery_state


def test_quality_agent_graph_maps_checks_to_delivery_state(tmp_path, monkeypatch):
    run_dir = _create_planning_run(tmp_path)
    _write_generated_project(run_dir / "generated-project")
    _patch_tool_lookup(monkeypatch, lambda name: name)
    calls: list[list[str]] = []

    def fake_executor(
        command: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    state = initial_delivery_state(run_id="qa-graph-test", run_dir=run_dir)

    result = run_quality_agent_graph(state, command_executor=fake_executor)

    assert result["stage"] == "qa"
    assert result["status"] == "qa_passed"
    assert result["qa_status"] == "passed"
    assert result["completed_nodes"] == ["qa"]
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "08-qa-report.md",
        "qa/test-plan.json",
        "qa/results.json",
        "qa/commands.log",
        "qa/docker/build-summary.json",
    ]
    assert ["uv", "sync", "--frozen"] in calls
    assert ["docker", "compose", "config"] in calls
    assert any(event["event"] == "qa_completed" for event in _read_events(run_dir))


def test_quality_agent_graph_exposes_expected_node_order():
    assert QUALITY_AGENT_GRAPH_NODE_ORDER == [
        "prepare_context",
        "check_existing_evidence",
        "prepare_evidence",
        "build_test_plan",
        "artifact_checks",
        "static_security_checks",
        "python_checks",
        "docker_checks",
        "browser_checks",
        "summarize_results",
        "write_report",
        "apply_result",
    ]


def test_quality_agent_graph_mermaid_includes_internal_nodes():
    mermaid = render_quality_agent_graph_mermaid()

    assert "artifact_checks" in mermaid
    assert "python_checks" in mermaid
    assert "docker_checks" in mermaid
    assert "browser_checks" in mermaid
    assert "write_report" in mermaid


def test_quality_agent_graph_requires_nodes():
    with pytest.raises(ValueError, match="requires at least one node"):
        build_quality_agent_graph(node_order=[])


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
    return run_pipeline(requirements, tmp_path / "runs", run_id="qa-graph-test")


def _write_generated_project(target_dir: Path) -> None:
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
    for relative_path, content in files.items():
        path = target_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


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
