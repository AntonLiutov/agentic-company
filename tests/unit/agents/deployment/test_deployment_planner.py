import json

from agentic_company.agents.deployment import write_deployment_plan, write_deployment_request
from agentic_company.agents.deployment.planner import (
    build_deployment_plan,
    build_deployment_request,
)


def test_deployment_plan_shell_does_not_classify_topology(tmp_path):
    target_dir = tmp_path / "generated-project"
    target_dir.mkdir()
    (target_dir / "docker-compose.yml").write_text(
        "services:\n  random-service:\n    build: .\n",
        encoding="utf-8",
    )
    (target_dir / "README.md").write_text("# App\n", encoding="utf-8")

    plan = build_deployment_plan(target_dir)

    assert plan["runtime"] == "L6 Codex Deployment Agent"
    assert plan["status"] == "codex_required"
    assert plan["topology_owner"] == "deployment-codex-agent"
    assert "docker-compose.yml" in plan["observed_files"]
    assert "topology" not in plan
    assert "deployment_targets" not in plan


def test_deployment_request_shell_keeps_environment_keys_without_commands(tmp_path):
    target_dir = tmp_path / "generated-project"
    target_dir.mkdir()
    (target_dir / ".env.example").write_text("API_BASE_URL=http://api:8000\n", encoding="utf-8")

    request = build_deployment_request(target_dir)

    assert request["runtime"] == "L6 Codex Deployment Agent"
    assert request["status"] == "codex_required"
    assert request["topology_owner"] == "deployment-codex-agent"
    assert request["environment_variables_from_example"] == ["API_BASE_URL"]
    assert "commands" not in request
    assert "deployment_targets" not in request


def test_write_deployment_shell_artifacts(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)

    plan_artifacts = write_deployment_plan(run_dir, target_dir)
    request_artifacts = write_deployment_request(run_dir, target_dir)

    plan = json.loads((run_dir / "11-deployment-plan.json").read_text(encoding="utf-8"))
    request = json.loads((run_dir / "12-deployment-request.json").read_text(encoding="utf-8"))

    assert plan_artifacts == ["11-deployment-plan.json", "11-deployment-plan.md"]
    assert request_artifacts == ["12-deployment-request.json", "12-deployment-request.md"]
    assert plan["status"] == "codex_required"
    assert request["status"] == "codex_required"
    assert "does not hardcode" in (run_dir / "11-deployment-plan.md").read_text(encoding="utf-8")
