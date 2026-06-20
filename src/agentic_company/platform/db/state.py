"""Shared delivery graph state."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NotRequired, TypedDict
from uuid import uuid4

DELIVERY_STATE_SNAPSHOT = Path("delivery") / "run-state.json"


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
    public_urls: NotRequired[list[str]]
    post_deploy_qa_status: NotRequired[str | None]
    post_deploy_repair_attempts: NotRequired[int]
    repair_attempts: int
    max_repair_attempts: int
    blockers: list[str]
    auto_confirmations: list[str]
    completed_nodes: list[str]
    team_lead_sprint_id: NotRequired[str]
    agent_call_message_id: NotRequired[str | None]
    agent_call_correlation_id: NotRequired[str | None]
    handoff_scope: NotRequired[str]
    handoff_sprint_id: NotRequired[str]
    handoff_output_dir: NotRequired[str]
    handoff_expected_outputs: NotRequired[list[str]]
    final_project_report: NotRequired[str]
    final_project_artifacts: NotRequired[list[str]]
    codex_threads_by_agent: NotRequired[dict[str, str]]


def initial_delivery_state(
    *,
    run_id: str,
    run_dir: str | Path,
    requirements_path: str | Path | None = None,
    target_project_dir: str | Path | None = None,
    max_repair_attempts: int = 5,
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
        "public_urls": [],
        "post_deploy_qa_status": None,
        "post_deploy_repair_attempts": 0,
        "repair_attempts": 0,
        "max_repair_attempts": max_repair_attempts,
        "blockers": [],
        "auto_confirmations": [],
        "completed_nodes": [],
        "team_lead_sprint_id": "sprint-01",
        "codex_threads_by_agent": {},
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
    completed_nodes = list(state.get("completed_nodes", []))
    if node_name not in completed_nodes:
        completed_nodes.append(node_name)
    updated["completed_nodes"] = completed_nodes
    return updated


def codex_resume_thread_id(state: DeliveryState, agent_id: str) -> str:
    """Return the latest Codex thread id recorded for an agent role."""

    threads = state.get("codex_threads_by_agent", {})
    if not isinstance(threads, dict):
        return ""
    return str(threads.get(agent_id) or "")


def record_codex_thread(state: DeliveryState, agent_id: str, thread_id: str) -> None:
    """Record the latest Codex thread id for a resumable agent role."""

    if not thread_id:
        return
    threads = dict(state.get("codex_threads_by_agent", {}))
    threads[agent_id] = thread_id
    state["codex_threads_by_agent"] = threads


def write_delivery_state(state: DeliveryState, path: str | Path | None = None) -> Path:
    """Persist the internal graph state snapshot in DB and as a file export."""

    state_path = (
        Path(path) if path is not None else Path(state["run_dir"]) / DELIVERY_STATE_SNAPSHOT
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_name(f"{state_path.name}.{uuid4().hex}.tmp")
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    temp_path.write_text(payload, encoding="utf-8")
    for attempt in range(5):
        try:
            temp_path.replace(state_path)
            break
        except PermissionError:
            if attempt == 4:
                state_path.write_text(payload, encoding="utf-8")
                temp_path.unlink(missing_ok=True)
                break
            time.sleep(0.05 * (attempt + 1))
    from agentic_company.platform.db.runtime_db import record_delivery_state_snapshot

    record_delivery_state_snapshot(dict(state))
    return state_path
