"""Artifact references used by graph state and future run manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypedDict

from agentic_company.platform.models import ExecutionRequest

ArtifactKind = Literal[
    "planning",
    "execution",
    "qa",
    "deployment",
    "handoff",
    "log",
    "evidence",
    "internal",
]
ArtifactVisibility = Literal["user", "developer", "internal"]


class ArtifactRef(TypedDict):
    """Small, serializable pointer to a run artifact."""

    path: str
    kind: ArtifactKind
    owner_agent: str
    visibility: ArtifactVisibility


def artifact_ref(
    path: str,
    *,
    kind: ArtifactKind,
    owner_agent: str,
    visibility: ArtifactVisibility = "user",
) -> ArtifactRef:
    """Create a normalized artifact reference for delivery state."""

    return {
        "path": path,
        "kind": kind,
        "owner_agent": owner_agent,
        "visibility": visibility,
    }


def load_execution_request(run_dir: Path) -> ExecutionRequest:
    """Load the Fullstack Agent execution request for a run directory."""

    payload = json.loads((run_dir / "06-execution-request.json").read_text(encoding="utf-8"))
    return ExecutionRequest(
        run_id=str(payload["run_id"]),
        agent_id=str(payload["agent_id"]),
        agent_version=str(payload["agent_version"]),
        maturity_level=str(payload["maturity_level"]),
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        target_project_dir=str(payload["target_project_dir"]),
        input_artifacts=list(payload["input_artifacts"]),
        expected_outputs=list(payload["expected_outputs"]),
        instructions=list(payload["instructions"]),
        constraints=list(payload["constraints"]),
        project_archetype=str(payload.get("project_archetype", "single-service-streamlit")),
        feature_queue=list(payload.get("feature_queue", [])),
        active_feature=payload.get("active_feature"),
        completed_feature_ids=list(payload.get("completed_feature_ids", [])),
    )
