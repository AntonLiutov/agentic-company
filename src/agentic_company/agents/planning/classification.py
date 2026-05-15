"""Project classification rules for early deterministic workflows."""

from __future__ import annotations

from agentic_company.agents.planning.models import IntakeBrief, ProjectClassification


def classify_project(brief: IntakeBrief) -> ProjectClassification:
    stack = {item.lower() for item in brief.preferred_stack}
    feature_count = len(brief.core_features)
    is_api_web = _is_api_web_project(brief)

    if is_api_web:
        project_type = "multi-service-web-app-mvp"
        delivery_mode = "lean-dev-cloud-mvp" if "azure container apps" in stack else "lean-mvp"
    elif "streamlit" in stack:
        project_type = "web-app-mvp"
        delivery_mode = "lean-local-mvp"
    else:
        project_type = "web-app-mvp"
        delivery_mode = "lean-mvp"

    if is_api_web:
        complexity = "medium"
    elif feature_count <= 6 and "database persistence" in {
        item.lower() for item in brief.non_goals
    }:
        complexity = "low"
    elif feature_count <= 10:
        complexity = "medium"
    else:
        complexity = "high"

    return ProjectClassification(
        project_type=project_type,
        complexity=complexity,
        delivery_mode=delivery_mode,
        rationale=[
            "The requirements describe a small user-facing web application.",
            f"The preferred stack is {', '.join(brief.preferred_stack) or 'unspecified'}.",
            f"The MVP lists {feature_count} core features.",
        ],
    )


def _is_api_web_project(brief: IntakeBrief) -> bool:
    stack = {item.lower() for item in brief.preferred_stack}
    text = " ".join([brief.goal, *brief.core_features, *brief.acceptance_criteria]).lower()
    has_api = "fastapi" in stack or "api service" in text or "api and web" in text
    has_web = "streamlit" in stack or "web ui" in text or "web service" in text
    return has_api and has_web
