import json

from agentic_company.agents.deployment import write_deployment_plan, write_deployment_request
from agentic_company.agents.deployment.planner import build_deployment_plan


def test_deployment_plan_detects_container_ready_project(tmp_path):
    target_dir = tmp_path / "generated-project"
    target_dir.mkdir()
    (target_dir / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (target_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (target_dir / ".env.example").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    (target_dir / "README.md").write_text("# App\n", encoding="utf-8")

    plan = build_deployment_plan(target_dir)

    assert plan["readiness"] == "ready_for_container_review"
    assert plan["recommended_target"] == "azure-container-apps"
    assert plan["blockers"] == []
    assert "post-deploy smoke test" in " ".join(plan["next_steps"])


def test_write_deployment_plan_creates_json_and_markdown(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)

    artifacts = write_deployment_plan(run_dir, target_dir)

    payload = json.loads((run_dir / "11-deployment-plan.json").read_text(encoding="utf-8"))
    markdown = (run_dir / "11-deployment-plan.md").read_text(encoding="utf-8")

    assert artifacts == ["11-deployment-plan.json", "11-deployment-plan.md"]
    assert payload["readiness"] == "not_ready"
    assert "Dockerfile is missing." in payload["blockers"]
    assert "# Deployment Plan" in markdown


def test_write_deployment_request_creates_azure_request_without_login(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    (target_dir / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (target_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (target_dir / ".env.example").write_text(
        "OPENAI_API_KEY=\nDEFAULT_MODEL=gpt-4o-mini\n",
        encoding="utf-8",
    )
    (target_dir / "README.md").write_text("# App\n", encoding="utf-8")

    artifacts = write_deployment_request(run_dir, target_dir)

    payload = json.loads((run_dir / "12-deployment-request.json").read_text(encoding="utf-8"))
    markdown = (run_dir / "12-deployment-request.md").read_text(encoding="utf-8")

    assert artifacts == ["12-deployment-request.json", "12-deployment-request.md"]
    assert payload["status"] == "ready"
    assert payload["deployment_mode"] == "dev_reuse"
    assert payload["azure_login_required"] is True
    assert payload["login_required_when"] == "before running the future deployment runner"
    assert payload["inputs"]["resource_group"] == "rg-agentic-generated-dev"
    assert payload["inputs"]["container_registry"] == "agenticgenerateddevacr"
    assert payload["inputs"]["container_app_environment"] == "agentic-generated-dev-env"
    assert payload["inputs"]["container_app_name"] == "app-generated-project-dev"
    assert (
        payload["inputs"]["image"] == "agenticgenerateddevacr.azurecr.io/generated-project:latest"
    )
    assert payload["inputs"]["environment_variables"] == ["DEFAULT_MODEL", "OPENAI_API_KEY"]
    assert "az account show" in " ".join(check["command"] for check in payload["preflight_checks"])
    assert "az containerapp create" in " ".join(payload["commands"])
    assert "# Deployment Request" in markdown
