"""Artifact references used by graph state and future run manifests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict

from agentic_company.platform.models import ExecutionRequest

EXECUTION_REQUEST_ARTIFACT = "delivery/execution-request.json"

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


def read_json_artifact(path: Path, *, normalize_bom: bool = False) -> object:
    """Read a JSON artifact while tolerating UTF-8 BOM output from external tools."""

    has_bom = path.read_bytes().startswith(b"\xef\xbb\xbf")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if has_bom and normalize_bom:
        write_json_artifact(path, payload)
    return payload


def read_text_artifact(path: Path) -> str:
    """Read a text artifact produced by external tools.

    Codex and shell commands can emit Markdown/log snippets using the host
    terminal encoding. Prefer UTF-8, but tolerate common Windows text output so
    a display/report artifact cannot crash orchestration after the tool already
    completed successfully.
    """

    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_json_object_artifact(path: Path, *, normalize_bom: bool = False) -> dict[str, object]:
    """Read a JSON object artifact, returning an empty object for non-object payloads."""

    payload = read_json_artifact(path, normalize_bom=normalize_bom)
    return payload if isinstance(payload, dict) else {}


def write_json_artifact(path: Path, payload: object) -> None:
    """Write normalized UTF-8 JSON without BOM."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_execution_request(run_dir: Path) -> ExecutionRequest:
    """Load the current delivery execution request for a run directory."""

    payload = read_json_object_artifact(run_dir / EXECUTION_REQUEST_ARTIFACT, normalize_bom=True)
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
        feature_queue=list(payload.get("feature_queue", [])),
        active_feature=payload.get("active_feature"),
        completed_feature_ids=list(payload.get("completed_feature_ids", [])),
        execution_id=str(payload.get("execution_id") or ""),
        execution_intent=str(payload.get("execution_intent") or ""),
        parent_message_id=str(payload.get("parent_message_id") or ""),
        codex_resume_thread_id=str(payload.get("codex_resume_thread_id") or ""),
    )


def build_execution_request_payload(
    delivery_state: Mapping[str, Any],
    *,
    agent_id: str,
    model: str,
    input_artifacts: Sequence[str],
    expected_outputs: Sequence[str],
    instructions: Sequence[str],
    constraints: Sequence[str],
    target_project_dir: str | None = None,
    active_feature: Mapping[str, Any] | None = None,
    codex_resume_thread_id: str = "",
    agent_version: str = "0.1.0",
    maturity_level: str = "L6 Codex Agent",
    provider: str = "codex",
) -> dict[str, Any]:
    """Build a Codex execution request from the current delivery state.

    Specialist graphs own when to call this; Codex runners own how to execute it.
    """

    resolved_target_project_dir = (
        target_project_dir
        or delivery_state.get("target_project_dir")
        or str(Path(str(delivery_state["run_dir"])) / "generated-project")
    )
    return {
        "run_id": str(delivery_state["run_id"]),
        "agent_id": agent_id,
        "agent_version": agent_version,
        "maturity_level": maturity_level,
        "provider": provider,
        "model": model,
        "target_project_dir": str(resolved_target_project_dir),
        "input_artifacts": list(input_artifacts),
        "expected_outputs": list(expected_outputs),
        "instructions": list(instructions),
        "constraints": list(constraints),
        "feature_queue": list(delivery_state.get("feature_queue", [])),
        "active_feature": dict(active_feature) if active_feature is not None else None,
        "completed_feature_ids": list(delivery_state.get("completed_feature_ids", [])),
        "execution_id": str(delivery_state.get("agent_execution_id") or ""),
        "execution_intent": str(delivery_state.get("agent_execution_intent") or ""),
        "parent_message_id": str(delivery_state.get("agent_call_message_id") or ""),
        "codex_resume_thread_id": codex_resume_thread_id,
    }


def write_execution_request(run_dir: Path, payload: Mapping[str, Any]) -> None:
    """Write the current Codex execution request envelope."""

    write_json_artifact(run_dir / EXECUTION_REQUEST_ARTIFACT, dict(payload))


def update_execution_request_context(
    run_dir: Path,
    *,
    execution_id: str = "",
    execution_intent: str = "",
    parent_message_id: str = "",
    codex_resume_thread_id: str = "",
    feature_queue: list[dict[str, Any]] | None = None,
    active_feature: dict[str, Any] | None = None,
    completed_feature_ids: list[str] | None = None,
) -> None:
    """Persist the current tool-call execution context into the run request."""

    request_path = run_dir / EXECUTION_REQUEST_ARTIFACT
    if not request_path.exists():
        return
    payload = read_json_object_artifact(request_path, normalize_bom=True)
    if execution_id:
        payload["execution_id"] = execution_id
    if execution_intent:
        payload["execution_intent"] = execution_intent
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    payload["codex_resume_thread_id"] = codex_resume_thread_id
    if feature_queue is not None:
        payload["feature_queue"] = feature_queue
    if active_feature is not None:
        payload["active_feature"] = active_feature
    if completed_feature_ids is not None:
        payload["completed_feature_ids"] = completed_feature_ids
    write_json_artifact(request_path, payload)
