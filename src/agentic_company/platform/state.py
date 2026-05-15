"""Shared delivery graph state."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, TypedDict

from agentic_company.platform.artifacts import ArtifactRef


class DeliveryState(TypedDict):
    """Serializable state passed between company delivery graph nodes."""

    run_id: str
    run_dir: str
    target_project_dir: NotRequired[str | None]
    requirements_path: NotRequired[str | None]
    stage: str
    status: str
    qa_status: NotRequired[str | None]
    deployment_status: NotRequired[str | None]
    public_url: NotRequired[str | None]
    repair_attempts: int
    max_repair_attempts: int
    artifacts: list[ArtifactRef]
    blockers: list[str]
    auto_confirmations: list[str]
    completed_nodes: list[str]
    project_archetype: NotRequired[str]
    feature_queue: NotRequired[list[dict[str, Any]]]
    active_feature_id: NotRequired[str | None]
    completed_feature_ids: NotRequired[list[str]]
    feature_statuses: NotRequired[dict[str, str]]
    feature_repair_attempts: NotRequired[dict[str, int]]


def initial_delivery_state(
    *,
    run_id: str,
    run_dir: str | Path,
    requirements_path: str | Path | None = None,
    target_project_dir: str | Path | None = None,
    max_repair_attempts: int = 3,
) -> DeliveryState:
    """Build a complete initial state for the delivery graph."""

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target_project_dir": str(target_project_dir) if target_project_dir else None,
        "requirements_path": str(requirements_path) if requirements_path else None,
        "stage": "initialized",
        "status": "initialized",
        "qa_status": None,
        "deployment_status": None,
        "public_url": None,
        "repair_attempts": 0,
        "max_repair_attempts": max_repair_attempts,
        "artifacts": [],
        "blockers": [],
        "auto_confirmations": [],
        "completed_nodes": [],
        "project_archetype": "single-service-streamlit",
        "feature_queue": [],
        "active_feature_id": None,
        "completed_feature_ids": [],
        "feature_statuses": {},
        "feature_repair_attempts": {},
    }


def mark_node_completed(
    state: DeliveryState,
    *,
    node_name: str,
    stage: str,
    status: str = "running",
) -> DeliveryState:
    """Return state with a completed graph node recorded."""

    updated: DeliveryState = {**state}
    updated["stage"] = stage
    updated["status"] = status
    updated["completed_nodes"] = [*state.get("completed_nodes", []), node_name]
    return updated
