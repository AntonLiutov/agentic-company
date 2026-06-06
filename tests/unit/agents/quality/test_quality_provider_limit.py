import subprocess

from agentic_company.agents.quality.codex_cli import QualityCodexRunner
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifacts import (
    build_execution_request_payload,
    write_execution_request,
)
from agentic_company.platform.runtime_db import materialize_planning_items


def test_quality_provider_limit_does_not_register_missing_report_artifacts(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    target_project_dir.mkdir(parents=True)
    _setup_run(run_dir, monkeypatch)
    write_execution_request(
        run_dir,
        build_execution_request_payload(
            {
                "run_id": "run",
                "run_dir": str(run_dir),
                "target_project_dir": str(target_project_dir),
            },
            agent_id="qa-agent",
            model="gpt-5.5",
            input_artifacts=[],
            expected_outputs=["08-qa-report-US-1.md", "qa/results-US-1.json"],
            instructions=[],
            constraints=[],
            target_project_dir=str(target_project_dir),
            work_item={
                "work_item_id": "US-1",
                "title": "Feature",
                "sprint_id": "sprint-01",
                "acceptance_criteria": [],
            },
        ),
    )

    calls = 0

    def usage_limited(command, prompt, timeout_seconds, log_path, raw_events_path):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                '{"type":"error","message":"You\'ve hit your usage limit. '
                'Visit https://chatgpt.com/codex/settings/usage to purchase more credits."}'
            ),
            stderr="",
        )

    result = QualityCodexRunner(
        command_executor=usage_limited,
        contract_attempts=2,
    ).run(run_dir)

    assert calls == 1
    assert result.status == "qa_provider_limit"
    assert result.blocking_findings == [
        "QA could not run for work item US-1: provider usage limit reached."
    ]
    assert "08-qa-report-US-1.md" not in result.output_artifacts
    assert "qa/results-US-1.json" not in result.output_artifacts
    assert not any(path.endswith("/summary.md") for path in result.output_artifacts)


def _setup_run(run_dir, monkeypatch):
    db_path = run_dir.parent / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENTIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="qa@example.test",
        username="qa-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="QA",
        request_text="QA",
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
    materialize_planning_items("run")
