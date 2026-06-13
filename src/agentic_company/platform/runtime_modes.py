"""Runtime mode policy contracts for Phase 1.

Modes describe which agent gates are required for a run. They are data, not
branching logic: coordinators and future external board adapters can read the
same policy without inventing mode-specific strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunMode(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    ENTERPRISE = "enterprise"


class RiskMode(StrEnum):
    SAFE = "safe"
    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True, slots=True)
class ModePolicy:
    run_mode: RunMode
    required_agents: tuple[str, ...]
    requires_planning: bool
    requires_architecture: bool
    requires_deployment: bool
    requires_approval_gates: bool
    default_risk_mode: RiskMode


MODE_POLICIES: dict[RunMode, ModePolicy] = {
    RunMode.SIMPLE: ModePolicy(
        run_mode=RunMode.SIMPLE,
        required_agents=("fullstack-agent", "qa-agent", "documentation-handoff-agent"),
        requires_planning=False,
        requires_architecture=False,
        requires_deployment=False,
        requires_approval_gates=False,
        default_risk_mode=RiskMode.ASSISTED,
    ),
    RunMode.MEDIUM: ModePolicy(
        run_mode=RunMode.MEDIUM,
        required_agents=(
            "project-manager-agent",
            "fullstack-agent",
            "qa-agent",
            "documentation-handoff-agent",
        ),
        requires_planning=True,
        requires_architecture=False,
        requires_deployment=False,
        requires_approval_gates=False,
        default_risk_mode=RiskMode.ASSISTED,
    ),
    RunMode.COMPLEX: ModePolicy(
        run_mode=RunMode.COMPLEX,
        required_agents=(
            "business-analyst-agent",
            "architect-agent",
            "project-manager-agent",
            "fullstack-agent",
            "qa-agent",
            "deployment-agent",
            "documentation-handoff-agent",
        ),
        requires_planning=True,
        requires_architecture=True,
        requires_deployment=True,
        requires_approval_gates=False,
        default_risk_mode=RiskMode.ASSISTED,
    ),
    RunMode.ENTERPRISE: ModePolicy(
        run_mode=RunMode.ENTERPRISE,
        required_agents=(
            "business-analyst-agent",
            "architect-agent",
            "project-manager-agent",
            "fullstack-agent",
            "qa-agent",
            "deployment-agent",
            "documentation-handoff-agent",
        ),
        requires_planning=True,
        requires_architecture=True,
        requires_deployment=True,
        requires_approval_gates=True,
        default_risk_mode=RiskMode.SAFE,
    ),
}


def mode_policy(value: str | RunMode) -> ModePolicy:
    """Return a mode policy, accepting current console legacy labels."""

    normalized = str(value.value if isinstance(value, RunMode) else value).strip().lower()
    aliases = {
        "simple_prototype": RunMode.SIMPLE,
        "ui_web_app": RunMode.MEDIUM,
        "internal_tool": RunMode.MEDIUM,
        "full_product": RunMode.COMPLEX,
    }
    run_mode = aliases[normalized] if normalized in aliases else RunMode(normalized)
    return MODE_POLICIES[run_mode]


__all__ = ["MODE_POLICIES", "ModePolicy", "RiskMode", "RunMode", "mode_policy"]
