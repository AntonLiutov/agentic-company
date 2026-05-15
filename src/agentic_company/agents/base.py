"""First-class delivery agent contracts and shared state helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentic_company.platform.artifacts import ArtifactKind, ArtifactRef, artifact_ref
from agentic_company.platform.state import DeliveryState, mark_node_completed


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Small metadata record for an agent exposed to orchestration and UI layers."""

    agent_id: str
    name: str
    runtime: str
    stage: str


class DeliveryAgent(Protocol):
    """State-in/state-out contract for agents composed by the delivery graph."""

    descriptor: AgentDescriptor

    def run(self, state: DeliveryState) -> DeliveryState:
        """Run an agent against delivery state and return updated state."""


def blocked_state(
    state: DeliveryState,
    *,
    node_name: str,
    stage: str,
    reason: str,
) -> DeliveryState:
    """Mark an agent node blocked and preserve the blocker reason in graph state."""

    updated = mark_node_completed(state, node_name=node_name, stage=stage, status="blocked")
    updated["blockers"] = [*state.get("blockers", []), reason]
    return updated


def extend_artifacts(state: DeliveryState, artifacts: list[ArtifactRef]) -> None:
    """Append artifact references to delivery state."""

    state["artifacts"] = [*state.get("artifacts", []), *artifacts]


def artifact_refs(
    paths: list[str],
    *,
    kind: ArtifactKind,
    owner_agent: str,
) -> list[ArtifactRef]:
    """Build artifact references for a runner result."""

    return [artifact_ref(path, kind=kind, owner_agent=owner_agent) for path in paths]


def target_project_dir(state: DeliveryState) -> Path:
    """Return the target generated-project directory for current delivery state."""

    target_dir = state.get("target_project_dir")
    if target_dir:
        return Path(target_dir)
    return Path(state["run_dir"]) / "generated-project"
