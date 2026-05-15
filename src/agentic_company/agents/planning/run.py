"""Command-line entry point for the first deterministic planning pipeline."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_company.agents.planning.classification import classify_project
from agentic_company.agents.planning.intake import parse_requirements
from agentic_company.agents.planning.team_assembly import assemble_team
from agentic_company.agents.planning.workflow_planning import (
    plan_workflow,
    render_implementation_brief,
)
from agentic_company.platform.logging import configure_logging
from agentic_company.platform.models import ExecutionRequest

LOGGER = logging.getLogger(__name__)


def run_pipeline(requirements_path: Path, output_root: Path, run_id: str | None = None) -> Path:
    LOGGER.info("Starting planning pipeline for %s", requirements_path)
    brief = parse_requirements(requirements_path)
    classification = classify_project(brief)
    staffing = assemble_team(classification)
    plan = plan_workflow(brief, staffing)

    output_dir = output_root / (run_id or _default_run_id(brief.project_name))
    output_dir.mkdir(parents=True, exist_ok=True)

    event_log = output_dir / "events.jsonl"
    _write_event(
        event_log,
        output_dir.name,
        "pipeline",
        "run_started",
        {"requirements_path": str(requirements_path)},
    )

    _write_json_artifact(
        output_dir,
        event_log,
        output_dir.name,
        "intake-agent",
        "01-intake-brief.json",
        brief.to_dict(),
    )
    _write_json_artifact(
        output_dir,
        event_log,
        output_dir.name,
        "project-classifier",
        "02-project-classification.json",
        classification.to_dict(),
    )
    _write_json_artifact(
        output_dir,
        event_log,
        output_dir.name,
        "team-assembler-agent",
        "03-staffing-decision.json",
        staffing.to_dict(),
    )
    _write_json_artifact(
        output_dir,
        event_log,
        output_dir.name,
        "workflow-planner",
        "04-workflow-plan.json",
        plan.to_dict(),
    )

    implementation_brief = output_dir / "05-implementation-brief.md"
    implementation_brief.write_text(
        render_implementation_brief(brief, staffing, plan),
        encoding="utf-8",
    )
    _record_artifact_written(
        event_log,
        output_dir.name,
        "tech-lead-agent",
        implementation_brief.name,
    )

    execution_request = build_execution_request(output_dir.name, output_dir, plan)
    _write_json_artifact(
        output_dir,
        event_log,
        output_dir.name,
        "fullstack-agent",
        "06-execution-request.json",
        execution_request.to_dict(),
    )
    _write_event(
        event_log,
        output_dir.name,
        "fullstack-agent",
        "execution_request_created",
        {
            "artifact": "06-execution-request.json",
            "target_project_dir": execution_request.target_project_dir,
            "provider": execution_request.provider,
            "model": execution_request.model,
        },
    )

    _write_event(event_log, output_dir.name, "pipeline", "run_completed", {})
    LOGGER.info("Completed planning pipeline at %s", output_dir)

    return output_dir


def build_execution_request(
    run_id: str,
    output_dir: Path,
    plan: object | None = None,
    *,
    active_feature_id: str | None = None,
    completed_feature_ids: list[str] | None = None,
) -> ExecutionRequest:
    project_archetype = getattr(plan, "project_archetype", "single-service-streamlit")
    project_name = str(getattr(plan, "project_name", "generated-app"))
    app_slug = _docker_app_slug(project_name)
    feature_queue = [
        feature.to_dict() if hasattr(feature, "to_dict") else dict(feature)
        for feature in getattr(plan, "feature_queue", [])
    ]
    active_feature = _select_active_feature(feature_queue, active_feature_id)
    completed_ids = completed_feature_ids or []
    expected_outputs = [
        "app.py",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
        "docker-compose.yml",
        ".env.example",
        ".streamlit/config.toml",
        "execution-summary.md",
    ]
    instructions = [
        "Read the implementation brief and create the smallest project that satisfies it.",
        "Work only inside the target project directory.",
        "Use uv-first project setup when generating Python app instructions.",
        "Include Docker Compose setup when Docker is not a non-goal.",
        "Use feature IDs from the workflow plan and implementation brief in the execution summary.",
        "Write a short execution summary when complete.",
    ]
    if project_archetype == "api-web-compose":
        expected_outputs = [
            "api/app.py",
            "web/app.py",
            "README.md",
            "pyproject.toml",
            "uv.lock",
            "Dockerfile.api",
            "Dockerfile.web",
            "docker-compose.yml",
            ".env.example",
            "execution-summary.md",
        ]
        instructions.extend(
            [
                "Generate an API service plus a web UI service for the active feature only.",
                "Preserve previous completed feature behavior when implementing a later feature.",
                "Use stable Docker Compose service names exactly: api and web.",
                f"Use Docker name prefix exactly: agentic-{app_slug}.",
                f"Use stable Docker image names exactly: agentic-{app_slug}-api:latest "
                f"and agentic-{app_slug}-web:latest.",
                f"Use stable container names exactly: agentic-{app_slug}-api and "
                f"agentic-{app_slug}-web.",
                "Do not invent additional containers unless the implementation brief explicitly "
                "asks for them.",
            ]
        )

    return ExecutionRequest(
        run_id=run_id,
        agent_id="fullstack-agent",
        agent_version="0.1.0",
        maturity_level="L6 Codex Agent",
        provider="codex",
        model="gpt-5.5",
        target_project_dir=str(output_dir / "generated-project"),
        input_artifacts=[
            "01-intake-brief.json",
            "02-project-classification.json",
            "03-staffing-decision.json",
            "04-workflow-plan.json",
            "05-implementation-brief.md",
        ],
        expected_outputs=expected_outputs,
        instructions=instructions,
        constraints=[
            "Do not add authentication, database persistence, or external deployment "
            "unless requested.",
            "Keep generated code small enough for a hackathon demo.",
            "Preserve clear setup instructions for required environment variables.",
            "Prefer pyproject.toml plus uv commands over slow pip-only setup.",
            "Do not bake secrets into Docker images; read them from local environment or .env.",
            "Do not install uv with pip in Docker; use an official uv image or prebuilt uv binary.",
            "Use Docker layer caching by copying dependency metadata before application code.",
            "Keep generated service, image, and container names stable across runs.",
        ],
        project_archetype=project_archetype,
        feature_queue=feature_queue,
        active_feature=active_feature,
        completed_feature_ids=completed_ids,
    )


def _select_active_feature(
    feature_queue: list[dict[str, Any]],
    active_feature_id: str | None,
) -> dict[str, Any] | None:
    if not feature_queue:
        return None
    if active_feature_id:
        for feature in feature_queue:
            if feature.get("id") == active_feature_id:
                return feature
    return sorted(feature_queue, key=lambda feature: int(feature.get("delivery_order", 0)))[0]


def _docker_app_slug(value: str) -> str:
    words = [
        word
        for word in _slugify(value).split("-")
        if word not in {"multi", "service", "services", "web", "app", "mvp", "internal"}
    ]
    slug = "-".join(words) or _slugify(value)
    return slug[:24].strip("-") or "app"


def _slugify(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "generated-app"


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run the web-app MVP planning pipeline.")
    parser.add_argument("requirements", type=Path, help="Path to a requirements markdown file.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs"),
        help="Directory where run artifacts are written.",
    )
    parser.add_argument("--run-id", help="Optional stable run id for repeatable output.")
    args = parser.parse_args()

    output_dir = run_pipeline(args.requirements, args.output_root, args.run_id)
    print(f"Wrote planning artifacts to {output_dir}")


def _write_json_artifact(
    output_dir: Path,
    event_log: Path,
    run_id: str,
    agent_id: str,
    filename: str,
    payload: dict[str, object],
) -> None:
    path = output_dir / filename
    _write_json(path, payload)
    _record_artifact_written(event_log, run_id, agent_id, filename)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _record_artifact_written(
    event_log: Path,
    run_id: str,
    agent_id: str,
    artifact: str,
) -> None:
    LOGGER.info("%s wrote %s", agent_id, artifact)
    _write_event(
        event_log,
        run_id,
        agent_id,
        "artifact_written",
        {"artifact": artifact},
    )


def _write_event(
    event_log: Path,
    run_id: str,
    agent_id: str,
    event: str,
    data: dict[str, Any],
) -> None:
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "agent_id": agent_id,
        "event": event,
        "data": data,
    }
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    LOGGER.info(
        "event_written run_id=%s agent=%s event=%s data_keys=%s",
        run_id,
        agent_id,
        event,
        sorted(data),
    )


def _default_run_id(project_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(
        character.lower() if character.isalnum() else "-" for character in project_name
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{timestamp}-{slug or 'project'}"


if __name__ == "__main__":
    main()
