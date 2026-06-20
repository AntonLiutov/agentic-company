"""Canonical work-item contracts shared by runtime and console DB code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEAD_PLANNING_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "id": "PLAN-01",
        "title": "Business Analysis",
        "sprint_id": "planning",
        "delivery_order": 1,
        "suggested_owner_agent": "business-analyst-agent",
        "source_refs": ["00-requirements.md"],
    },
    {
        "id": "PLAN-02",
        "title": "Solution Architecture",
        "sprint_id": "planning",
        "delivery_order": 2,
        "suggested_owner_agent": "architect-agent",
        "source_refs": ["upstream-planning/business-analysis.json"],
    },
    {
        "id": "PLAN-03",
        "title": "Delivery Planning",
        "sprint_id": "planning",
        "delivery_order": 3,
        "suggested_owner_agent": "project-manager-agent",
        "source_refs": [
            "upstream-planning/business-analysis.json",
            "upstream-planning/architecture.json",
        ],
    },
    {
        "id": "PLAN-04",
        "title": "Sprint Delivery Coordination",
        "sprint_id": "planning",
        "delivery_order": 4,
        "suggested_owner_agent": "team-lead-agent",
        "source_refs": ["upstream-planning/project-management/release-plan.json"],
    },
)

HEAD_WORK_ITEM_BY_NODE: dict[str, str] = {
    "business_analyst": "PLAN-01",
    "architecture": "PLAN-02",
    "project_management": "PLAN-03",
    "team_lead": "PLAN-04",
}


def pm_work_items_from_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    """Load PM work items from the PM artifact contract for DB materialization."""

    queue_path = run_dir / "upstream-planning" / "project-management" / "planned-work-items.json"
    try:
        payload = json.loads(queue_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Missing PM work-item contract artifact: {queue_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid PM work-item contract JSON: {queue_path}") from exc
    if not isinstance(payload, list):
        raise ValueError("PM work-item contract must be a JSON array.")
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(payload, start=1):
        if not isinstance(raw, dict):
            errors.append(f"item[{index}] must be an object")
            continue
        item_id = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or raw.get("name") or "").strip()
        sprint_id = str(raw.get("sprint_id") or "").strip()
        owner_agent = str(raw.get("suggested_owner_agent") or raw.get("owner_agent") or "").strip()
        delivery_order = raw.get("delivery_order")
        item_errors: list[str] = []
        if not item_id:
            item_errors.append(f"item[{index}] missing id")
        if not title:
            item_errors.append(f"item[{index}] missing title")
        if not sprint_id:
            item_errors.append(f"item[{index}] missing sprint_id")
        if delivery_order in (None, ""):
            item_errors.append(f"item[{index}] missing delivery_order")
        if not owner_agent:
            item_errors.append(f"item[{index}] missing suggested_owner_agent")
        if item_errors:
            errors.extend(item_errors)
            continue
        items.append(
            {
                "id": item_id,
                "title": title,
                "sprint_id": sprint_id,
                "delivery_order": _int_value(delivery_order, index),
                "status": str(raw.get("status") or "todo"),
                "suggested_owner_agent": owner_agent,
                "source_refs": _string_list(raw.get("source_refs", [])),
                "acceptance_criteria": _string_list(raw.get("acceptance_criteria", [])),
                "definition_of_done": _string_list(raw.get("definition_of_done", [])),
                "dependencies": _string_list(raw.get("dependencies", [])),
                "qa_notes": _string_list(raw.get("qa_notes", [])),
                "deployment_notes": str(raw.get("deployment_notes") or ""),
            }
        )
    if errors:
        raise ValueError("Invalid PM work-item contract: " + "; ".join(errors))
    if not items:
        raise ValueError("PM work-item contract produced no work items.")
    return items


def pm_sprints_from_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    """Load PM sprint metadata from the PM artifact contract for DB materialization."""

    release_plan_path = run_dir / "upstream-planning" / "project-management" / "release-plan.json"
    try:
        payload = json.loads(release_plan_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ValueError(f"Missing PM release-plan contract artifact: {release_plan_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid PM release-plan contract JSON: {release_plan_path}") from exc
    raw_sprints = payload.get("sprints", []) if isinstance(payload, dict) else []
    if not isinstance(raw_sprints, list):
        raise ValueError("PM release-plan contract must include a sprints JSON array.")
    sprints: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_sprints, start=1):
        if not isinstance(raw, dict):
            errors.append(f"sprint[{index}] must be an object")
            continue
        sprint_id = str(raw.get("sprint_id") or raw.get("id") or "").strip()
        title = str(raw.get("title") or raw.get("name") or "").strip()
        delivery_order = raw.get("delivery_order")
        sprint_errors: list[str] = []
        if not sprint_id:
            sprint_errors.append(f"sprint[{index}] missing sprint_id")
        if not title:
            sprint_errors.append(f"sprint[{index}] missing title")
        if delivery_order in (None, ""):
            sprint_errors.append(f"sprint[{index}] missing delivery_order")
        if sprint_errors:
            errors.extend(sprint_errors)
            continue
        sprints.append(
            {
                "sprint_id": sprint_id,
                "title": title,
                "delivery_order": _int_value(delivery_order, index),
                "status": str(raw.get("status") or "planned"),
                "is_final": bool(raw.get("is_final") or raw.get("is_final_sprint")),
                "source_refs": ["upstream-planning/project-management/release-plan.json"],
            }
        )
    if errors:
        raise ValueError("Invalid PM release-plan contract: " + "; ".join(errors))
    if not sprints:
        raise ValueError("PM release-plan contract produced no sprints.")
    return sprints


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]
