"""Team Lead tool contracts and runtime environment helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from agentic_company.platform.agent_runtime import agent_env_value
from agentic_company.platform.state import DeliveryState

TeamLeadToolName = Literal[
    "run_fullstack",
    "run_qa",
    "run_deployment",
    "run_post_deploy_qa",
    "run_handoff",
    "codex_review",
    "inspect_sprint_status",
    "complete_sprint",
    "block_sprint",
]

TEAM_LEAD_TOOLS: tuple[TeamLeadToolName, ...] = (
    "run_fullstack",
    "run_qa",
    "run_deployment",
    "run_post_deploy_qa",
    "run_handoff",
    "codex_review",
    "inspect_sprint_status",
    "complete_sprint",
    "block_sprint",
)


@dataclass(frozen=True, slots=True)
class TeamLeadDecision:
    """Recorded Team Lead tool call decision."""

    tool: TeamLeadToolName
    reason: str
    target: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def env_value(key: str, delivery_state: DeliveryState) -> str:
    """Read runtime config from process env, run env, or repo env."""

    return agent_env_value(key, delivery_state)
