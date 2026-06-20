"""Lightweight team presets for Phase 3 operator selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TeamPreset(StrEnum):
    SMALL = "small"
    STANDARD = "standard"
    LARGE = "large"


@dataclass(frozen=True, slots=True)
class RoleSpec:
    role_id: str
    display_name: str
    stage: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class TeamSpec:
    preset: TeamPreset
    roles: tuple[RoleSpec, ...]
    advisory_note: str = ""


STANDARD_ROLES: tuple[RoleSpec, ...] = (
    RoleSpec("business-analyst-agent", "Business Analyst", "planning"),
    RoleSpec("architect-agent", "Solution Architect", "planning"),
    RoleSpec("project-manager-agent", "Delivery Planner", "planning"),
    RoleSpec("team-lead-agent", "Delivery Lead", "delivery"),
    RoleSpec("fullstack-agent", "Builder", "delivery"),
    RoleSpec("qa-agent", "Quality Reviewer", "quality"),
    RoleSpec("deployment-agent", "Publisher", "deployment"),
    RoleSpec("documentation-handoff-agent", "Handoff", "handoff"),
    RoleSpec("codex-review-agent", "Codex Review", "review"),
)


TEAM_PRESETS: dict[TeamPreset, TeamSpec] = {
    TeamPreset.SMALL: TeamSpec(
        preset=TeamPreset.SMALL,
        roles=(
            RoleSpec("fullstack-agent", "Builder", "delivery"),
            RoleSpec("qa-agent", "Quality Reviewer", "quality"),
            RoleSpec("documentation-handoff-agent", "Handoff", "handoff"),
        ),
        advisory_note="Small skips heavyweight planning unless the coordinator escalates.",
    ),
    TeamPreset.STANDARD: TeamSpec(
        preset=TeamPreset.STANDARD,
        roles=STANDARD_ROLES,
        advisory_note="Standard reproduces the current full MVP roster.",
    ),
    TeamPreset.LARGE: TeamSpec(
        preset=TeamPreset.LARGE,
        roles=STANDARD_ROLES,
        advisory_note="Large keeps the fixed roster for MVP and increases planning rigor.",
    ),
}


def team_spec(value: str | TeamPreset) -> TeamSpec:
    preset = value if isinstance(value, TeamPreset) else TeamPreset(str(value or "standard").lower())
    return TEAM_PRESETS[preset]


def estimate_team_preset(*, complexity: str, requires_deployment: bool) -> TeamPreset:
    normalized = complexity.strip().lower()
    if normalized in {"complex", "large", "enterprise"} or requires_deployment:
        return TeamPreset.LARGE
    if normalized in {"simple", "small"}:
        return TeamPreset.SMALL
    return TeamPreset.STANDARD


__all__ = [
    "RoleSpec",
    "TeamPreset",
    "TeamSpec",
    "TEAM_PRESETS",
    "estimate_team_preset",
    "team_spec",
]
