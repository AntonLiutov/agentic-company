"""Deployment Agent planning artifacts."""

from __future__ import annotations

import json
from pathlib import Path

DEPLOYMENT_PLAN_JSON = "11-deployment-plan.json"
DEPLOYMENT_PLAN_MARKDOWN = "11-deployment-plan.md"
DEPLOYMENT_REQUEST_JSON = "12-deployment-request.json"
DEPLOYMENT_REQUEST_MARKDOWN = "12-deployment-request.md"
DEV_RESOURCE_GROUP = "rg-agentic-generated-dev"
DEV_CONTAINER_REGISTRY = "agenticgenerateddevacr"
DEV_CONTAINER_APP_ENVIRONMENT = "agentic-generated-dev-env"


def write_deployment_plan(run_dir: Path, target_dir: Path) -> list[str]:
    """Inspect generated deployment artifacts and write a deployment readiness plan."""

    payload = build_deployment_plan(target_dir)
    (run_dir / DEPLOYMENT_PLAN_JSON).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / DEPLOYMENT_PLAN_MARKDOWN).write_text(
        render_deployment_plan(payload),
        encoding="utf-8",
    )
    return [DEPLOYMENT_PLAN_JSON, DEPLOYMENT_PLAN_MARKDOWN]


def write_deployment_request(run_dir: Path, target_dir: Path) -> list[str]:
    """Write the structured deployment request for a future Azure runner."""

    plan = build_deployment_plan(target_dir)
    payload = build_deployment_request(target_dir, plan)
    (run_dir / DEPLOYMENT_REQUEST_JSON).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / DEPLOYMENT_REQUEST_MARKDOWN).write_text(
        render_deployment_request(payload),
        encoding="utf-8",
    )
    return [DEPLOYMENT_REQUEST_JSON, DEPLOYMENT_REQUEST_MARKDOWN]


def build_deployment_plan(target_dir: Path) -> dict[str, object]:
    has_dockerfile = (target_dir / "Dockerfile").exists()
    has_compose = any(
        (target_dir / name).exists() for name in ["docker-compose.yml", "compose.yml"]
    )
    has_env_example = (target_dir / ".env.example").exists()
    has_readme = (target_dir / "README.md").exists()
    recommended_target = "azure-container-apps" if has_dockerfile else "local-only"
    readiness = "ready_for_container_review" if has_dockerfile and has_compose else "not_ready"

    blockers: list[str] = []
    if not has_dockerfile:
        blockers.append("Dockerfile is missing.")
    if not has_compose:
        blockers.append("Docker Compose file is missing.")
    if not has_env_example:
        blockers.append(".env.example is missing.")
    if not has_readme:
        blockers.append("README.md is missing.")

    return {
        "agent_id": "deployment-agent",
        "runtime": "L0 Deterministic",
        "target_project_dir": str(target_dir),
        "readiness": readiness,
        "recommended_target": recommended_target,
        "detected_artifacts": {
            "Dockerfile": has_dockerfile,
            "Docker Compose": has_compose,
            ".env.example": has_env_example,
            "README.md": has_readme,
        },
        "blockers": blockers,
        "next_steps": _next_steps(recommended_target, blockers),
    }


def build_deployment_request(
    target_dir: Path, plan: dict[str, object] | None = None
) -> dict[str, object]:
    plan_payload = plan or build_deployment_plan(target_dir)
    blockers = plan_payload.get("blockers", [])
    status = "ready" if plan_payload.get("readiness") == "ready_for_container_review" else "blocked"
    if blockers:
        status = "blocked"

    project_name = _project_name(target_dir)
    app_name = _resource_name(f"{project_name}-dev", prefix="app", max_length=32)
    image_repository = _resource_name(project_name, prefix="", max_length=48).removeprefix("-")
    image_name = f"{DEV_CONTAINER_REGISTRY}.azurecr.io/{image_repository}:latest"
    env_keys = _env_example_keys(target_dir / ".env.example")

    return {
        "agent_id": "deployment-agent",
        "runtime": "L0 Deterministic",
        "status": status,
        "target": "azure-container-apps",
        "deployment_mode": "dev_reuse",
        "target_project_dir": str(target_dir),
        "source_plan": DEPLOYMENT_PLAN_JSON,
        "azure_login_required": status == "ready",
        "login_required_when": (
            "before running the future deployment runner"
            if status == "ready"
            else "after deployment blockers are resolved"
        ),
        "inputs": {
            "subscription_id": "<azure-subscription-id>",
            "location": "westeurope",
            "resource_group": DEV_RESOURCE_GROUP,
            "container_registry": DEV_CONTAINER_REGISTRY,
            "container_app_name": app_name,
            "container_app_environment": DEV_CONTAINER_APP_ENVIRONMENT,
            "image": image_name,
            "environment_variables": env_keys,
        },
        "preflight_checks": [
            {
                "name": "Azure CLI is installed",
                "command": "az --version",
                "required": True,
            },
            {
                "name": "Azure account is selected",
                "command": "az account show",
                "required": True,
            },
            {
                "name": "Docker CLI is installed",
                "command": "docker --version",
                "required": True,
            },
            {
                "name": "Docker daemon is running",
                "command": "docker info",
                "required": True,
            },
            {
                "name": "Generated project has container artifacts",
                "command": (
                    'powershell -Command "if (!(Test-Path Dockerfile) -or '
                    '!(Test-Path docker-compose.yml)) { exit 1 }"'
                ),
                "required": True,
            },
        ],
        "commands": _deployment_commands(
            resource_group=DEV_RESOURCE_GROUP,
            location="westeurope",
            registry_name=DEV_CONTAINER_REGISTRY,
            app_name=app_name,
            environment_name=DEV_CONTAINER_APP_ENVIRONMENT,
            image_name=image_name,
            env_keys=env_keys,
        ),
        "blockers": list(blockers) if isinstance(blockers, list) else [],
        "notes": [
            "Do not bake secrets into the Docker image.",
            "Use generated-project/.env only as the local source for app secrets.",
            (
                "Dev mode intentionally reuses one resource group, registry, "
                "and Container Apps environment."
            ),
            (
                "The current platform should ask the user to confirm the Azure subscription "
                "before executing these commands."
            ),
            "Post-deploy QA should run Playwright against the public Container Apps URL.",
        ],
    }


def render_deployment_plan(payload: dict[str, object]) -> str:
    artifacts = payload.get("detected_artifacts", {})
    artifact_lines = []
    if isinstance(artifacts, dict):
        artifact_lines = [
            f"- {name}: {'yes' if present else 'no'}" for name, present in artifacts.items()
        ]
    blockers = payload.get("blockers", [])
    blocker_lines = [f"- {item}" for item in blockers] if isinstance(blockers, list) else []
    next_steps = payload.get("next_steps", [])
    next_step_lines = [f"- {item}" for item in next_steps] if isinstance(next_steps, list) else []

    return f"""# Deployment Plan

Status: {payload.get("readiness", "unknown")}

Recommended target: `{payload.get("recommended_target", "unknown")}`

Target project:
`{payload.get("target_project_dir", "")}`

## Detected Artifacts

{chr(10).join(artifact_lines) or "- No deployment artifacts inspected."}

## Blockers

{chr(10).join(blocker_lines) or "- No deployment blockers detected for container review."}

## Next Steps

{chr(10).join(next_step_lines) or "- No next steps generated."}
"""


def render_deployment_request(payload: dict[str, object]) -> str:
    inputs = payload.get("inputs", {})
    blockers = payload.get("blockers", [])
    checks = payload.get("preflight_checks", [])
    commands = payload.get("commands", [])
    notes = payload.get("notes", [])
    env_keys = []
    if isinstance(inputs, dict):
        env_keys = inputs.get("environment_variables", [])

    input_lines = []
    if isinstance(inputs, dict):
        input_lines = [
            f"- {key}: `{value}`" for key, value in inputs.items() if key != "environment_variables"
        ]
    env_lines = [f"- `{key}`" for key in env_keys] if isinstance(env_keys, list) else []
    blocker_lines = [f"- {item}" for item in blockers] if isinstance(blockers, list) else []
    check_lines = []
    if isinstance(checks, list):
        check_lines = [
            f"- {item.get('name', 'Check')}: `{item.get('command', '')}`"
            for item in checks
            if isinstance(item, dict)
        ]
    command_lines = []
    if isinstance(commands, list):
        command_lines = [
            f"{index}. `{item}`"
            for index, item in enumerate(commands, start=1)
            if isinstance(item, str)
        ]
    note_lines = [f"- {item}" for item in notes] if isinstance(notes, list) else []

    return f"""# Deployment Request

Status: {payload.get("status", "unknown")}

Target: `{payload.get("target", "unknown")}`

Target project:
`{payload.get("target_project_dir", "")}`

Azure login required: `{payload.get("azure_login_required", False)}`

Login required when: {payload.get("login_required_when", "unknown")}

## Inputs

{chr(10).join(input_lines) or "- No deployment inputs generated."}

## Application Environment Variables

{chr(10).join(env_lines) or "- No application environment variables detected."}

## Blockers

{chr(10).join(blocker_lines) or "- No deployment request blockers detected."}

## Preflight Checks

{chr(10).join(check_lines) or "- No preflight checks generated."}

## Command Plan

{chr(10).join(command_lines) or "- No commands generated."}

## Notes

{chr(10).join(note_lines) or "- No notes generated."}
"""


def _next_steps(recommended_target: str, blockers: list[str]) -> list[str]:
    if blockers:
        return [
            "Resolve deployment blockers in the generated project.",
            "Rerun QA before attempting deployment.",
        ]
    if recommended_target == "azure-container-apps":
        return [
            "Keep secrets in environment configuration, not in the image.",
            "Build and push the image to a registry.",
            "Create an Azure Container Apps environment.",
            "Deploy the container with required environment variables.",
            "Run a post-deploy smoke test against the public endpoint.",
        ]
    return ["Add Docker artifacts before selecting a cloud deployment target."]


def _env_example_keys(path: Path) -> list[str]:
    if not path.exists():
        return []
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key:
            keys.append(key)
    return sorted(set(keys))


def _resource_name(value: str, prefix: str, max_length: int) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    parts = [part for part in normalized.split("-") if part]
    suffix_length = max_length - len(prefix) - 1 if prefix else max_length
    suffix = "-".join(parts)
    if len(suffix) > suffix_length:
        head_length = max(8, suffix_length // 2)
        tail_length = suffix_length - head_length - 1
        suffix = f"{suffix[:head_length].strip('-')}-{suffix[-tail_length:].strip('-')}"
    suffix = suffix.strip("-") or "generated-project"
    return f"{prefix}-{suffix}" if prefix else suffix


def _project_name(target_dir: Path) -> str:
    metadata_path = target_dir.parent / "01-intake-brief.json"
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        project_name = str(payload.get("project_name", "")).strip()
        if project_name:
            return project_name
    return target_dir.name


def _deployment_commands(
    *,
    resource_group: str,
    location: str,
    registry_name: str,
    app_name: str,
    environment_name: str,
    image_name: str,
    env_keys: list[str],
) -> list[str]:
    env_arguments = " ".join(f"{key}=secretref:{key.lower().replace('_', '-')}" for key in env_keys)
    secret_arguments = " ".join(f"{key.lower().replace('_', '-')}=$env:{key}" for key in env_keys)
    return [
        'az account set --subscription "<azure-subscription-id>"',
        f"az group create --name {resource_group} --location {location}",
        (
            f"az acr create --resource-group {resource_group} --name {registry_name} "
            "--sku Basic --admin-enabled true"
        ),
        f"az acr login --name {registry_name}",
        f"docker build -t {image_name} .",
        f"docker push {image_name}",
        (
            f"az containerapp env create --name {environment_name} "
            f"--resource-group {resource_group} --location {location}"
        ),
        (
            f"az containerapp create --name {app_name} --resource-group {resource_group} "
            f"--environment {environment_name} --image {image_name} --target-port 8501 "
            f"--ingress external --secrets {secret_arguments or '<no-secrets>'} "
            f"--env-vars {env_arguments or '<no-env-vars>'}"
        ),
        (
            f"az containerapp show --name {app_name} --resource-group {resource_group} "
            "--query properties.configuration.ingress.fqdn -o tsv"
        ),
    ]
