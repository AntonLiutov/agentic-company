import subprocess
from pathlib import Path

from agentic_company.agents.fullstack.codex_cli import CodexCliRunner
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifacts import write_execution_request


def test_fullstack_runner_does_not_return_generated_project_sources_as_evidence(
    tmp_path: Path,
    monkeypatch,
):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    target_project_dir.mkdir(parents=True)
    (target_project_dir / "README.md").write_text("# App\n", encoding="utf-8")
    web_dir = target_project_dir / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<main>App</main>\n", encoding="utf-8")
    (web_dir / "app.js").write_text("console.log('app')\n", encoding="utf-8")
    (web_dir / "styles.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    cache_dir = target_project_dir / "node_modules" / "pkg"
    cache_dir.mkdir(parents=True)
    (cache_dir / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="fullstack@example.test",
        username="fullstack-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Fullstack",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )
    write_execution_request(
        run_dir,
        {
            "run_id": "run",
            "agent_id": "fullstack-agent",
            "agent_version": "0.1.0",
            "maturity_level": "L6 Codex Agent",
            "provider": "codex",
            "model": "gpt-5.5",
            "target_project_dir": str(target_project_dir),
            "input_artifacts": [],
            "expected_outputs": ["README.md"],
            "instructions": [],
            "constraints": [],
            "work_item": {"work_item_id": "F1", "title": "Build app", "sprint_id": "sprint-01"},
        },
    )

    runner = CodexCliRunner(
        command_executor=lambda *_args: subprocess.CompletedProcess(
            args=["codex"],
            returncode=0,
            stdout="Implemented app.",
            stderr="",
        )
    )

    result = runner.run(run_dir)

    assert "generated-project/README.md" not in result.output_artifacts
    assert "generated-project/web/index.html" not in result.output_artifacts
    assert "generated-project/web/app.js" not in result.output_artifacts
    assert "generated-project/web/styles.css" not in result.output_artifacts
    assert "generated-project/node_modules/pkg/index.js" not in result.output_artifacts
    assert "README.md" not in result.output_artifacts
