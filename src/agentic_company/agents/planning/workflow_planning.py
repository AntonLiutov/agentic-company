"""Workflow planning for the first web-app MVP path."""

from __future__ import annotations

import re

from agentic_company.agents.planning.models import (
    FeatureWorkItem,
    IntakeBrief,
    StaffingDecision,
    WorkflowPhase,
    WorkflowPlan,
)


def plan_workflow(brief: IntakeBrief, staffing: StaffingDecision) -> WorkflowPlan:
    """Create a compact workflow plan for a web-app MVP."""
    project_archetype = _project_archetype(brief)
    feature_queue = _build_feature_queue(brief, project_archetype)
    if project_archetype == "api-web-compose":
        return WorkflowPlan(
            workflow_id="multi-service-api-web-mvp",
            project_name=brief.project_name,
            project_archetype=project_archetype,
            feature_queue=feature_queue,
            phases=[
                WorkflowPhase(
                    name="Product scope",
                    owner="Product Manager Agent",
                    outputs=[
                        "Feature queue",
                        "Feature acceptance criteria",
                        "Non-goals",
                        "Release scope",
                    ],
                ),
                WorkflowPhase(
                    name="Technical plan",
                    owner="Tech Lead Agent",
                    outputs=[
                        "API and web service structure",
                        "Environment variables",
                        "Docker Compose runtime",
                        "Implementation tasks by feature",
                    ],
                ),
                WorkflowPhase(
                    name="Implementation",
                    owner="Fullstack Agent",
                    outputs=[
                        "API service",
                        "Web service",
                        "Docker Compose setup",
                        "README setup notes",
                    ],
                ),
                WorkflowPhase(
                    name="QA review",
                    owner="QA Agent",
                    outputs=[
                        "Feature acceptance map",
                        "API checks",
                        "Web checks",
                        "Docker runtime checks",
                    ],
                ),
                WorkflowPhase(
                    name="Deployment",
                    owner="Deployment Agent",
                    outputs=[
                        "Topology-aware deployment plan",
                        "Stable dev resource mapping",
                        "Post-deployment QA request",
                    ],
                ),
                WorkflowPhase(
                    name="Handoff",
                    owner="Documentation / Handoff Agent",
                    outputs=[
                        "Delivered feature summary",
                        "Public URL",
                        "QA and deployment evidence",
                        "Next steps",
                    ],
                ),
            ],
        )

    return WorkflowPlan(
        workflow_id="web-app-mvp",
        project_name=brief.project_name,
        project_archetype=project_archetype,
        feature_queue=feature_queue,
        phases=[
            WorkflowPhase(
                name="Product scope",
                owner="Product Manager Agent",
                outputs=["MVP goal", "User stories", "Non-goals", "Acceptance criteria"],
            ),
            WorkflowPhase(
                name="Technical plan",
                owner="Tech Lead Agent",
                outputs=[
                    "App structure",
                    "Environment variables",
                    "Fast local setup with uv",
                    "Docker Compose runtime",
                    "Implementation tasks",
                ],
            ),
            WorkflowPhase(
                name="Implementation",
                owner="Fullstack Agent",
                outputs=["Streamlit app", "LLM client boundary", "README setup notes"],
            ),
            WorkflowPhase(
                name="QA review",
                owner="QA Agent",
                outputs=["Manual test checklist", "Known limitations"],
            ),
            WorkflowPhase(
                name="Handoff",
                owner="Documentation / Handoff Agent",
                outputs=["Run instructions", "Configuration notes", "Next steps"],
            ),
        ],
    )


def render_implementation_brief(
    brief: IntakeBrief,
    staffing: StaffingDecision,
    plan: WorkflowPlan,
) -> str:
    features = "\n".join(f"- {item}" for item in brief.core_features)
    config = _render_required_configuration(brief.required_configuration)
    criteria = "\n".join(f"- {item}" for item in brief.acceptance_criteria)
    agents = "\n".join(f"- {agent}" for agent in staffing.selected_agents)
    phases = "\n".join(
        f"- {phase.name}: {phase.owner} -> {', '.join(phase.outputs)}" for phase in plan.phases
    )
    feature_queue = _render_feature_queue(plan.feature_queue)
    delivery_notes = _render_delivery_notes(plan)

    return f"""# Implementation Brief: {brief.project_name}

## Goal

{brief.goal}

## Target User

{brief.target_user}

## MVP Features

{features}

## Required Configuration

{config}

## Acceptance Criteria

{criteria}

## Feature Queue

{feature_queue}

## Selected Team

{agents}

## Workflow

{phases}

## Delivery Notes

{delivery_notes}
"""


FEATURE_PREFIX_RE = re.compile(r"^(?P<id>F\d+|Feature\s+\d+)\s*[:\-]\s*(?P<text>.+)$", re.I)


def _project_archetype(brief: IntakeBrief) -> str:
    searchable = " ".join(
        [
            *brief.core_features,
            *brief.acceptance_criteria,
            *brief.preferred_stack,
            brief.goal,
        ]
    ).lower()
    has_api = _mentions_api_service(searchable)
    has_web = "web" in searchable or "streamlit" in searchable or "ui" in searchable
    if has_api and has_web:
        return "api-web-compose"
    return "single-service-streamlit"


def _mentions_api_service(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "fastapi",
            "endpoint",
            "api service",
            "api can ",
            "api and web",
            "api + web",
            "through the api",
        ]
    )


def _build_feature_queue(brief: IntakeBrief, project_archetype: str) -> list[FeatureWorkItem]:
    prefixed_features: list[tuple[str, str]] = []
    for feature in brief.core_features:
        match = FEATURE_PREFIX_RE.match(feature)
        if match:
            prefixed_features.append(
                (_normalize_feature_id(match.group("id")), match.group("text"))
            )

    criteria_by_feature: dict[str, list[str]] = {}
    unscoped_criteria: list[str] = []
    for criterion in brief.acceptance_criteria:
        match = FEATURE_PREFIX_RE.match(criterion)
        if match:
            feature_id = _normalize_feature_id(match.group("id"))
            criteria_by_feature.setdefault(feature_id, []).append(match.group("text"))
        else:
            unscoped_criteria.append(criterion)

    if prefixed_features:
        return [
            _feature_item(
                feature_id=feature_id,
                title=title,
                acceptance_criteria=criteria_by_feature.get(feature_id, unscoped_criteria),
                delivery_order=index,
                project_archetype=project_archetype,
            )
            for index, (feature_id, title) in enumerate(prefixed_features, start=1)
        ]

    title = "MVP feature set"
    if len(brief.core_features) == 1:
        title = brief.core_features[0]
    return [
        _feature_item(
            feature_id="F1",
            title=title,
            acceptance_criteria=brief.acceptance_criteria,
            delivery_order=1,
            project_archetype=project_archetype,
        )
    ]


def _feature_item(
    *,
    feature_id: str,
    title: str,
    acceptance_criteria: list[str],
    delivery_order: int,
    project_archetype: str,
) -> FeatureWorkItem:
    if project_archetype == "api-web-compose":
        test_notes = [
            "Map acceptance criteria to API endpoint checks.",
            "Map web UI criteria to browser checks.",
            "Verify API and web service integration through Docker Compose.",
        ]
        deployment_notes = [
            "Deploy API and web as a supported multi-service dev topology.",
            "Configure the web service with the deployed API base URL.",
        ]
    else:
        test_notes = [
            "Verify local app startup.",
            "Verify Docker runtime when Docker is in scope.",
            "Verify the browser flow for user-visible acceptance criteria.",
        ]
        deployment_notes = [
            "Deploy as a single supported web app when deployment is requested.",
        ]

    return FeatureWorkItem(
        id=feature_id,
        title=title,
        user_value=f"Delivers: {title}",
        acceptance_criteria=acceptance_criteria,
        dependencies=[],
        suggested_owner_agent="fullstack-agent",
        delivery_order=delivery_order,
        test_notes=test_notes,
        deployment_notes=deployment_notes,
    )


def _normalize_feature_id(raw_feature_id: str) -> str:
    normalized = raw_feature_id.upper().replace("FEATURE", "F").replace(" ", "")
    if normalized.startswith("F"):
        return normalized
    return f"F{normalized}"


def _render_feature_queue(feature_queue: list[FeatureWorkItem]) -> str:
    lines: list[str] = []
    for feature in feature_queue:
        lines.append(f"### {feature.id}: {feature.title}")
        lines.append("")
        lines.append(f"- Owner: {feature.suggested_owner_agent}")
        lines.append(f"- Delivery order: {feature.delivery_order}")
        lines.append("- Acceptance criteria:")
        lines.extend(f"  - {criterion}" for criterion in feature.acceptance_criteria)
        lines.append("- QA notes:")
        lines.extend(f"  - {note}" for note in feature.test_notes)
        lines.append("- Deployment notes:")
        lines.extend(f"  - {note}" for note in feature.deployment_notes)
        lines.append("")
    return "\n".join(lines).strip()


def _render_required_configuration(required_configuration: list[str]) -> str:
    if not required_configuration:
        return "- None required from the business user for planning."
    return "\n".join(f"- {item}" for item in required_configuration)


def _render_delivery_notes(plan: WorkflowPlan) -> str:
    if plan.project_archetype != "api-web-compose":
        return f"""- Project archetype: `{plan.project_archetype}`.
- Build the smallest local Streamlit app that satisfies the acceptance criteria.
- Use `DEFAULT_MODEL` with a default of `gpt-4o-mini` unless the project overrides it.
- Prefer `uv` for local setup and run instructions. Include `uv` commands in the generated
  README, such as `uv init`, `uv add`, and `uv run streamlit run app.py` when appropriate.
- Include `pip` setup only as a fallback, not as the primary path.
- If Docker Compose is not listed under non-goals, include a minimal `Dockerfile` and
  `docker-compose.yml` so the app can run with `docker compose up --build`.
- Docker should read runtime credentials from a local `.env` file and should not bake secrets
  into the image.
- Docker should be optimized for rebuild speed: do not install `uv` through `pip`; use an
  official uv image/prebuilt binary, include `uv.lock` when possible, and copy dependency
  metadata before app source.
- Docker dependency installs should use a BuildKit cache mount for uv downloads, such as
  `RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev`.
- After dependencies are synced during the image build, Docker runtime commands should use
  `uv run --no-sync ...` so container startup does not repeat dependency checks.
- Keep Python version requirements and package lower bounds stable unless the project truly
  needs newer versions; changing them invalidates the Docker dependency cache.
- Keep provider-specific execution behind a future runner boundary so Codex, Claude,
  Figma, or other tools can be added later.
"""
    app_slug = _docker_app_slug(plan.project_name)
    return f"""- Project archetype: `api-web-compose`.
- Generate a simple API service and a simple web UI service.
- Keep the generated project layout easy to inspect: `api/`, `web/`, shared project metadata,
  and Docker Compose at the project root.
- Use stable service names in Docker Compose: `api` and `web`.
- Use Docker name prefix: `agentic-{app_slug}`.
- Use stable Docker image names: `agentic-{app_slug}-api:latest` and
  `agentic-{app_slug}-web:latest`.
- Use stable container names: `agentic-{app_slug}-api` and
  `agentic-{app_slug}-web`.
- Use `API_BASE_URL=http://api:8000` as the local Docker Compose default for the web service.
- Treat `API_BASE_URL` as generated runtime configuration, not a value the business user must
  provide during planning.
- Keep feature behavior intentionally small; satisfy the feature queue before adding extras.
- Prefer `uv` for local setup and run instructions. Include `uv` commands in the generated README.
- Include `pip` setup only as a fallback, not as the primary path.
- Docker should read runtime configuration from a local `.env` file and should not bake secrets
  into the image.
- Docker should be optimized for rebuild speed: do not install `uv` through `pip`; use an
  official uv image/prebuilt binary, include `uv.lock` when possible, and copy dependency
  metadata before app source.
- Docker dependency installs should use a BuildKit cache mount for uv downloads, such as
  `RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev`.
- After dependencies are synced during the image build, Docker runtime commands should use
  `uv run --no-sync ...` so container startup does not repeat dependency checks.
- Keep Python version requirements and package lower bounds stable unless the project truly
  needs newer versions; changing them invalidates the Docker dependency cache.
- Keep provider-specific execution behind a future runner boundary so Codex, Claude,
  Figma, or other tools can be added later.
"""


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
