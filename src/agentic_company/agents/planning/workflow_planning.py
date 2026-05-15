"""Workflow planning for the first web-app MVP path."""

from __future__ import annotations

from agentic_company.agents.planning.models import (
    IntakeBrief,
    StaffingDecision,
    WorkflowPhase,
    WorkflowPlan,
)


def plan_workflow(brief: IntakeBrief, staffing: StaffingDecision) -> WorkflowPlan:
    """Create a compact workflow plan for a web-app MVP."""
    return WorkflowPlan(
        workflow_id="web-app-mvp",
        project_name=brief.project_name,
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
    config = "\n".join(f"- {item}" for item in brief.required_configuration)
    criteria = "\n".join(f"- {item}" for item in brief.acceptance_criteria)
    agents = "\n".join(f"- {agent}" for agent in staffing.selected_agents)
    phases = "\n".join(
        f"- {phase.name}: {phase.owner} -> {', '.join(phase.outputs)}" for phase in plan.phases
    )

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

## Selected Team

{agents}

## Workflow

{phases}

## Delivery Notes

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
