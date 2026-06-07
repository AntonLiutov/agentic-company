"""Generic deployment artifact helpers.

These helpers render non-prescriptive request shells. They do not detect
topology or choose deployment commands; the Deployment Agent owns those
decisions from project evidence at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

DEPLOYMENT_PLAN_JSON = "11-deployment-plan.json"
DEPLOYMENT_PLAN_MARKDOWN = "11-deployment-plan.md"
DEPLOYMENT_REQUEST_JSON = "12-deployment-request.json"
DEPLOYMENT_REQUEST_MARKDOWN = "12-deployment-request.md"
RELEASE_STRATEGY_BATCH = "release_batch"


def write_deployment_plan(run_dir: Path, target_dir: Path) -> list[str]:
    """Write a generic operator plan shell for the Deployment Codex Agent."""

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
    """Write a generic deployment request shell for the Deployment Codex Agent."""

    payload = build_deployment_request(target_dir)
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
    """Build a non-prescriptive plan shell.

    It records observable files and delegates topology/strategy selection to the
    Deployment Codex Agent.
    """

    return {
        "agent_id": "deployment-agent",
        "runtime": "L6 Codex Deployment Agent",
        "target_project_dir": str(target_dir),
        "release_strategy": RELEASE_STRATEGY_BATCH,
        "status": "codex_required",
        "topology_owner": "deployment-codex-agent",
        "observed_files": _observed_files(target_dir),
        "instructions": [
            "Deployment Codex must derive topology from project evidence.",
            "Deployment Codex must decide whether deployment is safe.",
            "Deployment Codex must write the final deployment contract artifacts.",
        ],
    }


def build_deployment_request(target_dir: Path) -> dict[str, object]:
    """Build a non-prescriptive request shell for deployment execution."""

    return {
        "agent_id": "deployment-agent",
        "runtime": "L6 Codex Deployment Agent",
        "status": "codex_required",
        "target_environment": "azure-container-apps-dev",
        "deployment_mode": "dev_reuse",
        "release_strategy": RELEASE_STRATEGY_BATCH,
        "target_project_dir": str(target_dir),
        "environment_variables_from_example": _env_example_keys(target_dir / ".env.example"),
        "topology_owner": "deployment-codex-agent",
        "constraints": [
            "Do not derive fixed service names, ports, Dockerfiles, or container count here.",
            "Do not delete cloud resources or user data without explicit approval.",
            "Do not print or bake secrets into images.",
            "Deployment reports URL targets; QA owns post-deployment validation.",
        ],
    }


def render_deployment_plan(payload: dict[str, object]) -> str:
    files = payload.get("observed_files", [])
    file_lines = [f"- `{item}`" for item in files] if isinstance(files, list) else []
    instructions = payload.get("instructions", [])
    instruction_lines = (
        [f"- {item}" for item in instructions] if isinstance(instructions, list) else []
    )
    return f"""# Deployment Plan

Status: {payload.get("status", "unknown")}

Runtime: {payload.get("runtime", "unknown")}

Release strategy: `{payload.get("release_strategy", "unknown")}`

Target project:
`{payload.get("target_project_dir", "")}`

## Topology Ownership

The Deployment Codex Agent owns topology discovery from generated project
evidence. This plan shell intentionally does not hardcode service names, ports,
Dockerfiles, or cloud commands.

## Observed Files

{chr(10).join(file_lines) or "- No files observed yet."}

## Instructions

{chr(10).join(instruction_lines) or "- No instructions."}
"""


def render_deployment_request(payload: dict[str, object]) -> str:
    env_keys = payload.get("environment_variables_from_example", [])
    env_lines = [f"- `{item}`" for item in env_keys] if isinstance(env_keys, list) else []
    constraints = payload.get("constraints", [])
    constraint_lines = (
        [f"- {item}" for item in constraints] if isinstance(constraints, list) else []
    )
    return f"""# Deployment Request

Status: {payload.get("status", "unknown")}

Runtime: {payload.get("runtime", "unknown")}

Target environment: `{payload.get("target_environment", "unknown")}`

Release strategy: `{payload.get("release_strategy", "unknown")}`

Target project:
`{payload.get("target_project_dir", "")}`

## Topology Ownership

Deployment Codex must inspect the generated project and decide how deployment
should work. This request shell is not a command plan.

## Environment Variables From Example

{chr(10).join(env_lines) or "- No `.env.example` keys detected."}

## Constraints

{chr(10).join(constraint_lines) or "- No constraints."}
"""


def _observed_files(target_dir: Path) -> list[str]:
    if not target_dir.exists():
        return []
    files: list[str] = []
    for path in sorted(target_dir.rglob("*")):
        if path.is_file():
            try:
                files.append(path.relative_to(target_dir).as_posix())
            except ValueError:
                continue
        if len(files) >= 200:
            files.append("... truncated ...")
            break
    return files


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
