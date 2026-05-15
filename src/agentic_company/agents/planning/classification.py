"""Project classification rules for early deterministic workflows."""

from __future__ import annotations

from agentic_company.agents.planning.models import IntakeBrief, ProjectClassification


def classify_project(brief: IntakeBrief) -> ProjectClassification:
    stack = {item.lower() for item in brief.preferred_stack}
    feature_count = len(brief.core_features)

    project_type = "web-app-mvp"
    if "streamlit" in stack:
        delivery_mode = "lean-local-mvp"
    else:
        delivery_mode = "lean-mvp"

    if feature_count <= 6 and "database persistence" in {item.lower() for item in brief.non_goals}:
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
