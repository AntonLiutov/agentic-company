"""Minimal primitives for selecting a delivery team."""

from agentic_company.agents.planning.models import ProjectClassification, StaffingDecision

LEAN_WEB_APP_TEAM = [
    "Product Manager Agent",
    "Tech Lead Agent",
    "Fullstack Agent",
    "QA Agent",
    "Documentation / Handoff Agent",
]


def assemble_team(classification: ProjectClassification) -> StaffingDecision:
    """Select the smallest useful team for the classified project."""
    optional_agents: list[str] = []
    if classification.complexity in {"medium", "high"}:
        optional_agents.extend(
            [
                "UX / Product Designer Agent",
                "Solution Architect Agent",
            ]
        )

    return StaffingDecision(
        project_type=classification.project_type,
        complexity=classification.complexity,
        delivery_mode=classification.delivery_mode,
        selected_agents=LEAN_WEB_APP_TEAM,
        optional_agents=optional_agents,
        rationale=[
            "Use one compact product/technical delivery team for the first MVP.",
            "Keep specialist roles optional until project ambiguity or risk increases.",
        ],
    )


def summarize_staffing(decision: StaffingDecision) -> str:
    """Return a compact human-readable summary."""
    return (
        f"{decision.project_type} | "
        f"complexity={decision.complexity} | "
        f"mode={decision.delivery_mode} | "
        f"agents={len(decision.selected_agents)}"
    )
