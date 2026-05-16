"""Head Agent tool contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

HeadToolName = Literal[
    "run_business_analyst",
    "run_architect",
    "run_project_manager",
    "run_team_lead",
    "codex_review",
    "inspect_delivery_status",
    "complete_delivery",
]

HEAD_TOOLS: tuple[HeadToolName, ...] = (
    "run_business_analyst",
    "run_architect",
    "run_project_manager",
    "run_team_lead",
    "codex_review",
    "inspect_delivery_status",
    "complete_delivery",
)


@dataclass(frozen=True, slots=True)
class HeadDecision:
    """Recorded Head Agent tool-call decision."""

    tool: HeadToolName
    reason: str
    target: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
