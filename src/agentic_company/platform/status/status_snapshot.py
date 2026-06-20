"""Deterministic DB-backed delivery status snapshots.

These snapshots are a fast read-model utility over canonical runtime DB tables.
Head and Team Lead still use the independent status inspector as the production
default; this module is not a silent replacement for that inspection contract.
"""

from __future__ import annotations

from typing import Any

from agentic_company.platform.db.runtime_db import (
    blocked_work_items,
    completed_work_item_ids,
    get_work_item,
    list_sprint_work_items,
    next_sprint_to_run,
    sprint_completion_state,
    sprint_ids,
)
from agentic_company.platform.db.state import DeliveryState


def build_sprint_status_snapshot(
    state: DeliveryState,
    *,
    sprint_id: str,
    work_item_id: str = "PLAN-04",
) -> dict[str, Any]:
    """Return a deterministic sprint status payload from DB canonical state."""

    run_id = str(state["run_id"])
    completion = sprint_completion_state(run_id, sprint_id)
    work_items = [item.to_dict() for item in list_sprint_work_items(run_id, sprint_id)]
    blocked = [item.to_dict() for item in blocked_work_items(run_id, sprint_id)]
    return {
        "scope": "sprint",
        "run_id": run_id,
        "sprint_id": sprint_id,
        "work_item_id": work_item_id,
        "sprint_status": completion.status or "missing",
        "can_complete_sprint": completion.is_complete,
        "has_items": completion.has_items,
        "is_final": completion.is_final,
        "next_work_item_id": completion.next_work_item_id,
        "completed_work_item_ids": completed_work_item_ids(run_id, sprint_id),
        "blocked_work_items": blocked,
        "work_items": work_items,
        "state_status": str(state.get("status") or ""),
        "state_blockers": list(state.get("blockers", [])),
        "qa_status": state.get("qa_status"),
        "deployment_status": state.get("deployment_status"),
        "post_deploy_qa_status": state.get("post_deploy_qa_status"),
        "public_url": state.get("public_url"),
    }


def build_delivery_status_snapshot(state: DeliveryState) -> dict[str, Any]:
    """Return a deterministic delivery status payload from DB canonical state."""

    run_id = str(state["run_id"])
    ids = sprint_ids(run_id)
    sprint_states = [sprint_completion_state(run_id, sprint_id) for sprint_id in ids]
    blocked = [item.to_dict() for item in blocked_work_items(run_id)]
    next_sprint_id = next_sprint_to_run(run_id)
    all_planned_sprints_complete = bool(sprint_states) and all(
        sprint.has_items and sprint.is_complete for sprint in sprint_states
    )
    delivery_status = (
        "ready_to_complete"
        if all_planned_sprints_complete and not blocked
        else "blocked"
        if blocked or any(sprint.is_blocked for sprint in sprint_states)
        else "running"
    )
    plan_04 = _optional_work_item(run_id, "PLAN-04")
    return {
        "scope": "delivery",
        "run_id": run_id,
        "delivery_status": delivery_status,
        "can_complete_delivery": delivery_status == "ready_to_complete",
        "next_sprint_to_run": next_sprint_id,
        "sprints": [sprint.to_dict() for sprint in sprint_states],
        "blocked_work_items": blocked,
        "completed_work_item_ids": completed_work_item_ids(run_id),
        "coordination_work_item": plan_04.to_dict() if plan_04 else None,
        "state_stage": str(state.get("stage") or ""),
        "state_status": str(state.get("status") or ""),
        "state_blockers": list(state.get("blockers", [])),
        "completed_nodes": list(state.get("completed_nodes", [])),
        "qa_status": state.get("qa_status"),
        "deployment_status": state.get("deployment_status"),
        "post_deploy_qa_status": state.get("post_deploy_qa_status"),
        "public_url": state.get("public_url"),
        "public_urls": list(state.get("public_urls", [])),
    }


def _optional_work_item(run_id: str, work_item_id: str):
    try:
        return get_work_item(run_id, work_item_id)
    except ValueError:
        return None
