import json
import subprocess
from pathlib import Path

from agentic_company.agents.architecture.codex_cli import (
    ARCHITECT_WORK_DIR,
    ArchitectCodexRunner,
    build_architecture_codex_prompt,
)
from agentic_company.agents.architecture.graph import (
    ARCHITECTURE_JSON,
    ARCHITECTURE_MD,
    ARCHITECTURE_MMD,
    ARCHITECTURE_REQUEST,
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
)
from agentic_company.console.web.db import ConsoleRepository


def test_architecture_prompt_scopes_codex_to_architecture_artifacts(tmp_path, monkeypatch):
    _register_run(tmp_path, monkeypatch)
    (tmp_path / "00-requirements.md").write_text("F1: Build task tracker.\n", encoding="utf-8")
    (tmp_path / BUSINESS_ANALYSIS_MD).parent.mkdir(parents=True)
    (tmp_path / BUSINESS_ANALYSIS_MD).write_text("# BA\n", encoding="utf-8")
    (tmp_path / BUSINESS_ANALYSIS_JSON).write_text(
        '{"product_goal":"Track tasks"}',
        encoding="utf-8",
    )
    request = {
        "run_id": "run",
        "model": "gpt-5.5",
        "requirements_artifact": "00-requirements.md",
        "input_artifacts": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
        "expected_outputs": [ARCHITECTURE_MD, ARCHITECTURE_JSON, ARCHITECTURE_MMD],
        "incoming_messages": (
            "- Message id: msg-head\n  From: head-agent\n  Content:\n    Use BA output."
        ),
        "available_agents": [
            {
                "agent_id": "architect-agent",
                "name": "Architect Agent",
                "stage": "architecture",
                "family": "technical-planning",
                "runtime": "L4 LangGraph Agent Executor + L6 Codex Architect",
            },
        ],
    }

    prompt = build_architecture_codex_prompt(request, tmp_path)

    assert ARCHITECTURE_MD in prompt
    assert ARCHITECTURE_JSON in prompt
    assert ARCHITECTURE_MMD in prompt
    assert "Write only the three allowed architecture artifacts" in prompt
    assert "Do not create sprint plans, planned work item contracts" in prompt
    assert "Available agent registry snapshot" in prompt
    assert "architect-agent: Architect Agent" in prompt
    assert "Head Agent coordinates this planning flow" in prompt
    assert "Azure deployment is a supported platform capability" in prompt
    assert "Scale architecture detail to the source complexity" in prompt
    assert "simple deployable architecture" in prompt
    assert "Treat application deployment as the normal delivery path" in prompt
    assert "Deployment Agent can inspect or create suitable dev resources" in prompt
    assert "Incoming coordinator messages" in prompt
    assert "Use BA output" in prompt
    assert "Use the registry snapshot only as context for internal JSON" in prompt
    assert "Treat platform execution details as internal coordination context" in prompt
    assert "Do not include tool write policy" in prompt
    assert "agent registry, orchestration routing, or AI-provider details" in prompt
    assert "Mermaid is the primary visual architecture artifact" in prompt
    assert "Markdown is a concise technical architecture brief" in prompt
    assert "Do not duplicate the full JSON contract in Markdown" in prompt
    assert "Do not invent runtime calls" in prompt
    assert "readable at thumbnail size" in prompt
    assert "Keep node labels short" in prompt
    assert "Avoid unexplained acronyms" in prompt
    assert "Prefer business-readable labels" in prompt
    assert "move the provider-specific\n  detail to Markdown and JSON" in prompt
    assert "Keep edge labels short" in prompt
    assert "when the connection is\n  obvious" in prompt
    assert "Do not place implementation verbs or acceptance-criteria text" in prompt
    assert "Draw only product/system architecture" in prompt
    assert "Do not include delivery workflow" in prompt
    assert "agent workflow, platform orchestration" in prompt
    assert "Use arrows only for meaningful runtime calls" in prompt
    assert "show it as a boundary/environment" in prompt
    assert "Put process notes, QA implications" in prompt
    assert "sprint plans, planned work item contracts, and delivery sequencing are not" in prompt
    assert "quality attributes" in prompt
    assert "technical_decisions" in prompt
    assert "Preserve every distinct feature/source label" in prompt
    assert "collapse many features into a smaller fixed set" in prompt
    assert "Do not overrule BA non-goals" in prompt


def test_architect_codex_runner_maps_valid_contract_to_completed_result(tmp_path, monkeypatch):
    _register_run(tmp_path, monkeypatch)
    (tmp_path / "00-requirements.md").write_text("Build a task tracker.\n", encoding="utf-8")
    (tmp_path / BUSINESS_ANALYSIS_MD).parent.mkdir(parents=True)
    (tmp_path / BUSINESS_ANALYSIS_MD).write_text("# BA\n", encoding="utf-8")
    (tmp_path / BUSINESS_ANALYSIS_JSON).write_text(
        json.dumps({"product_goal": "Track tasks"}),
        encoding="utf-8",
    )
    request_path = tmp_path / ARCHITECTURE_REQUEST
    request_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "agent_id": "architect-agent",
                "model": "gpt-5.5",
                "requirements_artifact": "00-requirements.md",
                "input_artifacts": [BUSINESS_ANALYSIS_MD, BUSINESS_ANALYSIS_JSON],
                "expected_outputs": [ARCHITECTURE_MD, ARCHITECTURE_JSON, ARCHITECTURE_MMD],
                "codex_resume_thread_id": "",
                "available_agents": [],
                "incoming_messages": "- No incoming coordinator messages were provided.",
            }
        ),
        encoding="utf-8",
    )

    def fake_command(
        command,
        prompt,
        timeout_seconds,
        log_path: Path,
        raw_events_path: Path,
    ):
        (tmp_path / ARCHITECTURE_MD).write_text("# Architecture\n", encoding="utf-8")
        (tmp_path / ARCHITECTURE_MMD).write_text(
            "flowchart LR\n  User --> Web\n",
            encoding="utf-8",
        )
        (tmp_path / ARCHITECTURE_JSON).write_text(
            json.dumps(
                {
                    "architecture_goal": "Define a simple task tracker architecture.",
                    "system_context": {},
                    "components": [],
                    "service_boundaries": [],
                    "data_model_direction": [],
                    "api_contract_direction": [],
                    "deployment_topology": [],
                    "provided_constraints": [],
                    "quality_attributes": [],
                    "technical_decisions": [],
                    "rejected_options": [],
                    "implementation_constraints": [],
                    "qa_implications": [],
                    "deployment_implications": [],
                    "risks": [],
                    "open_questions": [],
                    "coordination_notes": [],
                    "diagram": {"artifact": ARCHITECTURE_MMD},
                }
            ),
            encoding="utf-8",
        )
        log_path.write_text("done\n", encoding="utf-8")
        raw_events_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="Architecture complete.")

    result = ArchitectCodexRunner(command_executor=fake_command).run(tmp_path)

    assert result.status == "architecture_completed"
    assert ARCHITECTURE_MD in result.output_artifacts
    assert ARCHITECTURE_JSON in result.output_artifacts
    assert ARCHITECTURE_MMD in result.output_artifacts
    assert any(
        artifact.startswith((ARCHITECT_WORK_DIR / "codex").as_posix())
        for artifact in result.output_artifacts
    )
    assert result.blocking_findings == []


def _register_run(run_dir: Path, monkeypatch) -> None:
    db_path = run_dir / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="arch@example.test",
        username="arch-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Architecture",
        request_text="Design",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
