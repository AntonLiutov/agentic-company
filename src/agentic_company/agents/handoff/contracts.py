"""Typed contracts for Handoff Agent scopes and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

HandoffScope = Literal["sprint_handoff", "final_project_report"]

SPRINT_HANDOFF_SCOPE: HandoffScope = "sprint_handoff"
FINAL_PROJECT_REPORT_SCOPE: HandoffScope = "final_project_report"
VALID_HANDOFF_SCOPES = {SPRINT_HANDOFF_SCOPE, FINAL_PROJECT_REPORT_SCOPE}


@dataclass(frozen=True, slots=True)
class HandoffContractPaths:
    """Canonical handoff artifact paths for one handoff scope."""

    html: str

    def as_list(self) -> list[str]:
        return [self.html]


def handoff_contract_paths_for_scope(
    handoff_scope: str,
    *,
    sprint_id: str = "",
) -> HandoffContractPaths:
    """Return canonical handoff artifact paths for an explicit handoff scope."""

    if handoff_scope == SPRINT_HANDOFF_SCOPE:
        normalized_sprint_id = sprint_id.strip()
        if not normalized_sprint_id:
            raise ValueError("sprint_id is required for sprint_handoff.")
        return HandoffContractPaths(
            html=f"handoff/sprints/{normalized_sprint_id}/release-report.html",
        )
    if handoff_scope == FINAL_PROJECT_REPORT_SCOPE:
        if sprint_id.strip():
            raise ValueError("sprint_id must be empty for final_project_report.")
        return HandoffContractPaths(
            html="handoff/project/final/release-report.html",
        )
    raise ValueError(
        f"handoff_scope must be one of: {SPRINT_HANDOFF_SCOPE}, {FINAL_PROJECT_REPORT_SCOPE}."
    )
