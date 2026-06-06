"""Artifact references used by graph state and future run manifests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agentic_company.platform.models import ExecutionRequest

EXECUTION_REQUEST_ARTIFACT = "delivery/execution-request.json"

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
    """Load the current delivery execution request from the DB contract."""

    from agentic_company.platform.runtime_db import latest_execution_request

    payload = latest_execution_request(run_dir.name)
    return _execution_request_from_payload(payload)


def _execution_request_from_payload(payload: Mapping[str, Any]) -> ExecutionRequest:
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
        work_item=dict(payload.get("work_item") or {}),
        completed_work_item_ids=list(payload.get("completed_work_item_ids", [])),
        execution_id=str(payload.get("execution_id") or ""),
        execution_intent=str(payload.get("execution_intent") or ""),
        parent_message_id=str(payload.get("parent_message_id") or ""),
        codex_resume_thread_id=str(payload.get("codex_resume_thread_id") or ""),
        handoff_scope=str(payload.get("handoff_scope") or ""),
        handoff_sprint_id=str(payload.get("handoff_sprint_id") or ""),
        handoff_output_dir=str(payload.get("handoff_output_dir") or ""),
        handoff_expected_outputs=list(payload.get("handoff_expected_outputs") or []),
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
    target_project_dir: str,
    work_item: Mapping[str, Any] | None = None,
    completed_work_item_ids: Sequence[str] | None = None,
    codex_resume_thread_id: str = "",
    handoff_scope: str = "",
    handoff_sprint_id: str = "",
    handoff_output_dir: str = "",
    handoff_expected_outputs: Sequence[str] | None = None,
    agent_version: str = "0.1.0",
    maturity_level: str = "L6 Codex Agent",
    provider: str = "codex",
) -> dict[str, Any]:
    """Build a Codex execution request from the current delivery state.

    Specialist graphs own when to call this; Codex runners own how to execute it.
    """

    if not str(target_project_dir or "").strip():
        raise ValueError("Execution request requires explicit target_project_dir")
    payload = {
        "run_id": str(delivery_state["run_id"]),
        "agent_id": agent_id,
        "agent_version": agent_version,
        "maturity_level": maturity_level,
        "provider": provider,
        "model": model,
        "target_project_dir": str(target_project_dir),
        "input_artifacts": list(input_artifacts),
        "expected_outputs": list(expected_outputs),
        "instructions": list(instructions),
        "constraints": list(constraints),
        "work_item": dict(work_item or {}),
        "completed_work_item_ids": list(completed_work_item_ids or []),
        "execution_id": str(delivery_state.get("agent_execution_id") or ""),
        "execution_intent": str(delivery_state.get("agent_execution_intent") or ""),
        "parent_message_id": str(delivery_state.get("agent_call_message_id") or ""),
        "codex_resume_thread_id": codex_resume_thread_id,
    }
    if handoff_scope:
        payload["handoff_scope"] = handoff_scope
        payload["handoff_sprint_id"] = handoff_sprint_id
    if handoff_output_dir:
        payload["handoff_output_dir"] = handoff_output_dir
    if handoff_expected_outputs is not None:
        payload["handoff_expected_outputs"] = list(handoff_expected_outputs)
    return payload


def write_execution_request(run_dir: Path, payload: Mapping[str, Any]) -> None:
    """Persist the current Codex execution request envelope in DB and as an export."""

    normalized = dict(payload)
    from agentic_company.platform.runtime_db import record_execution_request

    record_execution_request(str(normalized["run_id"]), normalized)
    write_json_artifact(run_dir / EXECUTION_REQUEST_ARTIFACT, normalized)


def update_execution_request_context(
    run_dir: Path,
    *,
    execution_id: str = "",
    execution_intent: str = "",
    parent_message_id: str = "",
    codex_resume_thread_id: str = "",
    work_item: dict[str, Any] | None = None,
    completed_work_item_ids: list[str] | None = None,
    handoff_scope: str = "",
    handoff_sprint_id: str = "",
    handoff_output_dir: str = "",
    handoff_expected_outputs: list[str] | None = None,
) -> None:
    """Persist the current tool-call execution context into the DB request contract."""

    from agentic_company.platform.runtime_db import (
        latest_execution_request,
        record_execution_request,
    )

    try:
        payload = latest_execution_request(run_dir.name)
    except ValueError:
        return
    if execution_id:
        payload["execution_id"] = execution_id
    if execution_intent:
        payload["execution_intent"] = execution_intent
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    payload["codex_resume_thread_id"] = codex_resume_thread_id
    if work_item is not None:
        payload["work_item"] = work_item
    if completed_work_item_ids is not None:
        payload["completed_work_item_ids"] = completed_work_item_ids
    if handoff_scope:
        payload["handoff_scope"] = handoff_scope
    if handoff_sprint_id or handoff_scope:
        payload["handoff_sprint_id"] = handoff_sprint_id
    if handoff_output_dir:
        payload["handoff_output_dir"] = handoff_output_dir
    if handoff_expected_outputs is not None:
        payload["handoff_expected_outputs"] = handoff_expected_outputs
    record_execution_request(str(payload["run_id"]), payload)
    write_json_artifact(run_dir / EXECUTION_REQUEST_ARTIFACT, payload)
