"""Sprint execution contracts shared by orchestration agents."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentic_company.platform.state import DeliveryState

FeatureStatus = Literal[
    "backlog",
    "pending",
    "ready",
    "assigned",
    "in_progress",
    "implemented",
    "review",
    "in_qa",
    "qa_passed",
    "qa_failed",
    "done",
    "blocked",
    "deployed",
    "handoff_ready",
]
SprintStatus = Literal["pending", "running", "passed", "blocked", "deployed", "handoff_ready"]

WORK_BOARD_ARTIFACT = "team-lead/work-board.json"

HEAD_PLANNING_ITEMS = [
    {
        "id": "PLAN-01",
        "title": "Business analysis",
        "sprint_id": "planning",
        "delivery_order": 1,
        "suggested_owner_agent": "business-analyst-agent",
    },
    {
        "id": "PLAN-02",
        "title": "Solution architecture",
        "sprint_id": "planning",
        "delivery_order": 2,
        "suggested_owner_agent": "architect-agent",
    },
    {
        "id": "PLAN-03",
        "title": "Project management plan",
        "sprint_id": "planning",
        "delivery_order": 3,
        "suggested_owner_agent": "project-manager-agent",
    },
    {
        "id": "PLAN-04",
        "title": "Sprint delivery",
        "sprint_id": "delivery",
        "delivery_order": 4,
        "suggested_owner_agent": "team-lead-agent",
    },
]

HEAD_WORK_ITEM_BY_NODE = {
    "business_analyst": "PLAN-01",
    "architecture": "PLAN-02",
    "project_management": "PLAN-03",
    "team_lead": "PLAN-04",
}


@dataclass(slots=True)
class FeatureTask:
    """A single feature-level unit owned by Team Lead execution."""

    feature_id: str
    title: str
    acceptance_criteria: list[str] = field(default_factory=list)
    delivery_order: int = 0
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    qa_notes: list[str] = field(default_factory=list)
    deployment_notes: list[str] = field(default_factory=list)
    status: FeatureStatus = "pending"
    sprint_id: str = ""
    suggested_owner_agent: str = "fullstack-agent"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FeatureTask:
        return cls(
            feature_id=str(payload.get("id") or payload.get("feature_id")),
            title=str(payload.get("title") or payload.get("name") or "Untitled feature"),
            acceptance_criteria=_string_list(payload.get("acceptance_criteria", [])),
            delivery_order=int(payload.get("delivery_order", 0)),
            description=str(payload.get("description", payload.get("user_value", ""))),
            dependencies=_string_list(payload.get("dependencies", [])),
            qa_notes=_string_list(payload.get("qa_notes", [])),
            deployment_notes=_string_list(payload.get("deployment_notes", [])),
            status=str(payload.get("status", "pending")),  # type: ignore[arg-type]
            sprint_id=str(payload.get("sprint_id") or ""),
            suggested_owner_agent=str(payload.get("suggested_owner_agent") or "fullstack-agent"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["id"] = payload.pop("feature_id")
        return payload


@dataclass(slots=True)
class SprintPlan:
    """One ordered sprint package for Team Lead execution."""

    sprint_id: str
    title: str
    goal: str
    features: list[FeatureTask]
    exit_criteria: list[str] = field(default_factory=list)
    deployment_policy: Any = "deploy_after_sprint"
    is_final_sprint: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_id": self.sprint_id,
            "title": self.title,
            "goal": self.goal,
            "features": [feature.to_dict() for feature in self.features],
            "exit_criteria": self.exit_criteria,
            "deployment_policy": self.deployment_policy,
            "is_final_sprint": self.is_final_sprint,
        }


@dataclass(slots=True)
class TeamLeadResult:
    """Structured outcome written by the Team Lead Agent."""

    sprint_id: str
    status: SprintStatus | str
    completed_features: list[str] = field(default_factory=list)
    failed_features: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    deployment_status: str | None = None
    qa_status: str | None = None
    handoff_status: str | None = None
    next_recommended_action: str = ""
    artifact_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkBoardItem:
    """One Team Lead board card derived from PM work items and runtime state."""

    item_id: str
    title: str
    sprint_id: str
    status: str = "pending"
    lane: str = "todo"
    owner_agent: str = ""
    assigned_agent: str = ""
    delivery_order: int = 0
    story_points: int = 0
    active: bool = False
    dependencies: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    last_message_id: str = ""
    last_execution_id: str = ""
    blocker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkBoard:
    """Serializable sprint board visible to coordinators, specialists, and UI."""

    sprint_id: str
    active_item_id: str | None
    items: list[WorkBoardItem]
    status_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint_id": self.sprint_id,
            "active_item_id": self.active_item_id,
            "items": [item.to_dict() for item in self.items],
            "status_counts": self.status_counts,
        }


def sprint_from_feature_queue(
    feature_queue: list[dict[str, Any]],
    *,
    sprint_id: str = "sprint-01",
    run_dir: str | Path | None = None,
) -> SprintPlan:
    """Build the selected sprint plan from PM feature work items."""

    plan = load_sprint_plan(run_dir, sprint_id) if run_dir else {}
    selected = features_for_sprint(
        {"feature_queue": feature_queue, "run_dir": str(run_dir or "")},
        sprint_id,
    )
    selected = _ordered_by_plan(selected, plan)
    features = sorted(
        [FeatureTask.from_dict(feature) for feature in selected],
        key=lambda feature: feature.delivery_order,
    )
    return SprintPlan(
        sprint_id=sprint_id,
        title=str(plan.get("title") or "Current planned delivery sprint"),
        goal=str(
            plan.get("goal")
            or "Deliver the selected sprint package through owner work, QA, and required gates."
        ),
        features=features,
        exit_criteria=_string_list(plan.get("exit_criteria", ["All sprint features pass QA."])),
        deployment_policy=plan.get("deployment_policy", "deploy_after_sprint"),
        is_final_sprint=bool(plan.get("is_final_sprint", True)),
    )


def sprint_from_delivery_state(state: DeliveryState) -> SprintPlan:
    """Build the active Team Lead sprint from delivery state and PM artifacts."""

    sprint_id = str(state.get("team_lead_sprint_id") or _first_sprint_id(state) or "sprint-01")
    return sprint_from_feature_queue(
        list(state.get("feature_queue", [])),
        sprint_id=sprint_id,
        run_dir=state.get("run_dir"),
    )


def features_for_sprint(
    state: DeliveryState | dict[str, Any],
    sprint_id: str,
) -> list[dict[str, Any]]:
    """Return features for one sprint, falling back to unscoped queues only."""

    feature_queue = list(state.get("feature_queue", []))
    scoped = [
        feature for feature in feature_queue if str(feature.get("sprint_id") or "") == sprint_id
    ]
    if scoped:
        return sorted(scoped, key=lambda feature: int(feature.get("delivery_order", 0)))

    unscoped = [feature for feature in feature_queue if not feature.get("sprint_id")]
    if unscoped:
        return sorted(unscoped, key=lambda feature: int(feature.get("delivery_order", 0)))
    return []


def sync_work_board(
    state: DeliveryState,
    *,
    sprint_id: str | None = None,
) -> DeliveryState:
    """Write a normalized board snapshot into state from feature queue/status fields."""

    updated: DeliveryState = {**state}
    board = build_work_board(updated, sprint_id=sprint_id)
    updated["work_board"] = board.to_dict()
    return updated


def build_work_board(
    state: DeliveryState,
    *,
    sprint_id: str | None = None,
) -> WorkBoard:
    """Return a dashboard-friendly board from the current delivery state."""

    active_sprint_id = sprint_id or str(state.get("team_lead_sprint_id") or _first_sprint_id(state))
    active_item_id = state.get("active_feature_id")
    feature_statuses = {
        str(key): str(value) for key, value in dict(state.get("feature_statuses", {})).items()
    }
    completed = {str(feature_id) for feature_id in state.get("completed_feature_ids", [])}
    items = [
        _work_board_item(
            feature,
            active_sprint_id=active_sprint_id,
            active_item_id=str(active_item_id or ""),
            completed=completed,
            feature_statuses=feature_statuses,
            state=state,
        )
        for feature in _board_features(state)
    ]
    return WorkBoard(
        sprint_id=active_sprint_id,
        active_item_id=str(active_item_id) if active_item_id else None,
        items=items,
        status_counts=_status_counts(items),
    )


def set_work_item_status(
    state: DeliveryState,
    item_id: str,
    status: str,
    *,
    active: bool | None = None,
    sprint_id: str | None = None,
) -> DeliveryState:
    """Update the status map and synchronized board for one work item."""

    updated: DeliveryState = {**state}
    statuses = dict(updated.get("feature_statuses", {}))
    statuses[item_id] = status
    updated["feature_statuses"] = statuses
    if active is True:
        updated["active_feature_id"] = item_id
    elif active is False and updated.get("active_feature_id") == item_id:
        updated["active_feature_id"] = None
    return sync_work_board(updated, sprint_id=sprint_id)


def seed_head_work_board(state: DeliveryState) -> DeliveryState:
    """Ensure Head-level planning and delivery handoff items are visible from run start."""

    updated: DeliveryState = {**state}
    if not updated.get("work_items"):
        updated["work_items"] = [dict(item) for item in HEAD_PLANNING_ITEMS]
    return sync_work_board(updated, sprint_id=str(updated.get("team_lead_sprint_id") or ""))


def load_sprint_plan(run_dir: str | Path | None, sprint_id: str) -> dict[str, Any]:
    """Load a PM sprint-plan artifact when it exists."""

    if not run_dir:
        return {}
    path = Path(run_dir) / "upstream-planning" / "project-management" / f"{sprint_id}-plan.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _first_sprint_id(state: DeliveryState) -> str:
    for feature in sorted(
        list(state.get("feature_queue", [])),
        key=lambda item: int(item.get("delivery_order", 0)),
    ):
        sprint_id = str(feature.get("sprint_id") or "")
        if sprint_id:
            return sprint_id
    return ""


def _board_features(state: DeliveryState) -> list[dict[str, Any]]:
    work_items = [
        feature for feature in list(state.get("work_items", [])) if isinstance(feature, dict)
    ]
    if work_items and not state.get("feature_queue"):
        return sorted(
            work_items,
            key=lambda item: (
                int(item.get("delivery_order", 0)),
                str(item.get("sprint_id") or ""),
                str(item.get("id") or item.get("feature_id") or ""),
            ),
        )
    return sorted(
        [feature for feature in list(state.get("feature_queue", [])) if isinstance(feature, dict)],
        key=lambda item: (
            str(item.get("sprint_id") or ""),
            int(item.get("delivery_order", 0)),
            str(item.get("id") or item.get("feature_id") or ""),
        ),
    )


def _work_board_item(
    feature: dict[str, Any],
    *,
    active_sprint_id: str,
    active_item_id: str,
    completed: set[str],
    feature_statuses: dict[str, str],
    state: DeliveryState,
) -> WorkBoardItem:
    item_id = str(feature.get("id") or feature.get("feature_id") or "")
    status = feature_statuses.get(item_id) or str(feature.get("status") or "pending")
    if item_id in completed and status in {"", "pending", "assigned", "in_progress", "in_qa"}:
        status = "qa_passed"
    if item_id == active_item_id and status in {"", "pending"}:
        status = "active"
    return WorkBoardItem(
        item_id=item_id,
        title=str(feature.get("title") or feature.get("name") or "Untitled work item"),
        sprint_id=str(feature.get("sprint_id") or active_sprint_id),
        status=status or "pending",
        lane=_lane_for_status(status or "pending"),
        owner_agent=str(feature.get("suggested_owner_agent") or ""),
        assigned_agent=str(state.get("agent_execution_agent_id") or ""),
        delivery_order=_int_value(feature.get("delivery_order")),
        story_points=_int_value(feature.get("story_points")),
        active=item_id == active_item_id,
        dependencies=_string_list(feature.get("dependencies", [])),
        source_refs=_string_list(feature.get("source_refs", [])),
        artifact_refs=_artifact_refs_for_item(state, item_id),
        last_message_id=str(state.get("agent_call_message_id") or ""),
        last_execution_id=str(state.get("agent_execution_id") or ""),
        blocker=_blocker_for_item(state, item_id),
    )


def _artifact_refs_for_item(state: DeliveryState, item_id: str) -> list[str]:
    refs: list[str] = []
    for artifact in state.get("artifacts", []):
        path = str(artifact.get("path") or "")
        if path and (f"/{item_id}/" in path or f"-{item_id}" in path or path.endswith(item_id)):
            refs.append(path)
    return refs


def _blocker_for_item(state: DeliveryState, item_id: str) -> str:
    for blocker in state.get("blockers", []):
        text = str(blocker)
        if item_id and item_id in text:
            return text
    return ""


def _status_counts(items: list[WorkBoardItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _lane_for_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"backlog", "pending", "ready", "assigned", "active"}:
        return "todo"
    if normalized == "in_progress":
        return "doing"
    if normalized in {"implemented", "review"}:
        return "review"
    if normalized in {"in_qa", "qa_failed"}:
        return "qa"
    if normalized in {"qa_passed", "done", "deployed", "handoff_ready"}:
        return "done"
    if normalized in {"blocked", "failed"}:
        return "blocked"
    return "todo"


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ordered_by_plan(features: list[dict[str, Any]], plan: dict[str, Any]) -> list[dict[str, Any]]:
    ordered_feature_ids = _ordered_feature_ids(plan)
    if not ordered_feature_ids:
        return features
    order_by_id = {feature_id: index for index, feature_id in enumerate(ordered_feature_ids)}
    return sorted(
        features,
        key=lambda feature: order_by_id.get(
            str(feature.get("id") or feature.get("feature_id") or ""),
            int(feature.get("delivery_order", 0)),
        ),
    )


def _ordered_feature_ids(plan: dict[str, Any]) -> list[str]:
    ordered_features = plan.get("ordered_features")
    if not isinstance(ordered_features, list):
        return []
    feature_ids: list[str] = []
    for item in ordered_features:
        if isinstance(item, dict):
            feature_id = str(item.get("id") or item.get("feature_id") or "")
        else:
            feature_id = str(item or "")
        if feature_id:
            feature_ids.append(feature_id)
    return feature_ids


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]
