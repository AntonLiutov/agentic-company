"""Product-facing presentation helpers for the web console."""

from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import socket
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentic_company.console.support import (
    DeliveryOverview,
    DeploymentTarget,
    FeatureProgress,
    repo_root,
)
from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.platform.artifacts.artifact_registry import USER_FACING_VISIBILITIES

AGENT_MODEL_OPTIONS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
]

AGENT_PROVIDER_OPTIONS = [
    ("google_gemini", "Google Gemini"),
    ("openai", "OpenAI"),
]

GEMINI_MODEL_OPTIONS = [
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

CODEX_MODEL_OPTIONS = [
    DEFAULT_CODEX_MODEL,
    "gpt-5.4",
    "gpt-5.4-mini",
]

REASONING_OPTIONS = ["none", "low", "medium", "high", "xhigh"]
CODEX_SERVICE_TIERS = ["standard", "fast"]

AGENT_LABELS = {
    "Head Agent": "Coordinator",
    "Business Analyst": "Business Analyst",
    "Architect": "Solution Architect",
    "Project Manager": "Delivery Planner",
    "Team Lead Agent": "Delivery Lead",
    "Fullstack Agent": "Builder",
    "QA Agent": "Quality Reviewer",
    "Deployment Agent": "Publisher",
    "Documentation / Handoff Agent": "Release Reporter",
    "Codex Review": "Quality Review",
    "Status Inspector": "Status Check",
}

STATUS_LABELS = {
    "active": "In Progress",
    "architecture": "Solution Design",
    "architecture_completed": "Solution Ready",
    "blocked": "Blocked",
    "business_analysis": "Requirements",
    "business_analysis_completed": "Requirements Ready",
    "closed": "Done",
    "deployed": "Published",
    "deployment": "Publishing",
    "deployment_deployed": "Published",
    "done": "Done",
    "failed": "Needs Attention",
    "failed_to_start": "Could Not Start",
    "paused_codex_auth": "Paused: Codex Login",
    "paused_provider_limit": "Paused: Provider Limit",
    "fullstack": "Build",
    "fullstack_feature_implemented": "Build Ready",
    "handoff": "Release Report",
    "handoff_ready": "Release Report Ready",
    "head": "Coordinator",
    "head_delivery_completed": "Delivery Complete",
    "head_planning_completed": "Planning Complete",
    "in_progress": "In Progress",
    "implemented": "Built",
    "pending": "Pending",
    "planning": "Planning",
    "planning_ready": "Ready",
    "project_management": "Delivery Planning",
    "project_management_completed": "Delivery Plan Ready",
    "qa": "Quality Review",
    "qa_failed": "Needs Quality Fix",
    "qa_feature_failed_blocked": "Quality Blocked",
    "qa_feature_passed_next_feature_ready": "Quality Passed",
    "qa_passed": "Done",
    "ready": "Ready",
    "ready_for_handoff": "Ready for Release Report",
    "ready_for_next_sprint": "Ready for Next Sprint",
    "ready_to_complete": "Ready to Complete",
    "review": "Review",
    "running": "Running",
    "starting": "Starting",
    "team_lead": "Delivery Lead",
    "team_lead_feature_selected": "Feature Selected",
    "team_lead_sprint_handoff_ready": "Release Report Ready",
}

AGENT_ICON_FILES = {
    "Coordinator": "coordinator.png",
    "Business Analyst": "business-analyst.png",
    "Solution Architect": "solution-architect.png",
    "Delivery Planner": "delivery-planner.png",
    "Delivery Lead": "delivery-lead.png",
    "Builder": "builder.png",
    "Quality Reviewer": "quality-reviewer.png",
    "Publisher": "publisher.png",
    "Release Reporter": "release-reporter.png",
}

BOARD_COLUMNS = [
    ("todo", "To Do"),
    ("blocked", "Blocked"),
    ("in_progress", "In Progress"),
    ("qa", "Quality Review"),
    ("done", "Done"),
]

TECHNICAL_HINTS = (
    "run-state.json",
    "run-events.jsonl",
    "tool-call-events.jsonl",
    "model-call-events.jsonl",
    "execution.log",
    ".log",
    "prompt.md",
    "request.json",
    "evidence.json",
    "decisions/",
    "codex/",
)
USER_FACING_ARTIFACT_TYPES = {
    "architecture_deliverable",
    "requirements_brief",
    "architecture_report",
    "business_analysis_deliverable",
    "delivery_plan",
    "execution_summary",
    "project_management_deliverable",
    "qa_evidence",
    "qa_report",
    "repair_request",
    "deployment_summary",
    "release_report",
    "screenshot_evidence",
}
INTERNAL_ARTIFACT_FILENAMES = {
    ".env",
    ".env.example",
    "run-events.jsonl",
    "tool-call-events.jsonl",
    "model-call-events.jsonl",
    "package-lock.json",
    "pnpm-lock.yaml",
    "prompt.md",
    "pyproject.toml",
    "request.json",
    "uv.lock",
}
INTERNAL_ARTIFACT_PATH_PARTS = {
    ".deno-cache",
    ".npm-cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "codex",
    "decisions",
    "node_modules",
    "playwright-results",
    "test-results",
}
PLANNING_TOOL_ITEMS = {
    "run_business_analyst": ("PLAN-01", "Requirements brief", "Business Analyst", 1),
    "run_architect": ("PLAN-02", "Solution overview", "Solution Architect", 2),
    "run_project_manager": ("PLAN-03", "Delivery plan", "Delivery Planner", 3),
    "run_team_lead": ("PLAN-04", "Sprint delivery coordination", "Delivery Lead", 4),
}


@dataclass(frozen=True, slots=True)
class BoardCard:
    id: str
    title: str
    owner: str
    sprint: str
    status: str
    column: str
    artifact_count: int
    active: bool
    order: int = 0
    started_at: str = ""
    completed_at: str = ""
    elapsed_label: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactView:
    path: str
    label: str
    agent: str
    business_agent: str
    kind: str
    technical: bool
    phase: str
    task_id: str
    task_title: str
    artifact_id: str = ""
    visibility: str = "business"
    artifact_type: str = ""


@dataclass(frozen=True, slots=True)
class TaskDetail:
    card: BoardCard
    reports: list[ArtifactView]
    logs: list[str]
    activity_groups: list[dict[str, object]]


def business_agent_label(agent: str) -> str:
    normalized = {
        "head-agent": "Coordinator",
        "head-codex-review": "Quality Review",
        "business-analyst-agent": "Business Analyst",
        "architect-agent": "Solution Architect",
        "project-manager-agent": "Delivery Planner",
        "team-lead-agent": "Delivery Lead",
        "team-lead-codex-review": "Quality Review",
        "fullstack-agent": "Builder",
        "qa-agent": "Quality Reviewer",
        "qa-codex-agent": "Quality Reviewer",
        "deployment-agent": "Publisher",
        "deployment-codex-agent": "Publisher",
        "documentation-handoff-agent": "Release Reporter",
        "handoff-codex-agent": "Release Reporter",
    }
    default_value = agent.replace("-agent", "").replace("-", " ").title()
    return AGENT_LABELS.get(agent, normalized.get(agent, default_value))


def status_label(status: str) -> str:
    token = (status or "pending").strip().lower()
    if token in STATUS_LABELS:
        return STATUS_LABELS[token]
    return _remove_internal_names(token.replace("_", " ").replace("-", " ").title())


def work_status_label(status: str) -> str:
    token = (status or "").lower()
    if any(value in token for value in ("blocked", "failed", "error")):
        return "Blocked"
    done_tokens = ("qa_passed", "deployed", "handoff_ready", "closed", "done")
    if any(value in token for value in done_tokens):
        return "Done"
    if any(value in token for value in ("review", "inspect")):
        return "Quality Review"
    if "qa" in token or "quality" in token:
        return "Quality Review"
    if any(value in token for value in ("progress", "running", "active", "doing")):
        return "In Progress"
    return "To Do"


def _remove_internal_names(label: str) -> str:
    replacements = {
        "Head": "Coordinator",
        "Business Analysis": "Requirements",
        "Project Management": "Delivery Planning",
        "Fullstack": "Build",
        "Deployment": "Publishing",
        "Handoff": "Release Report",
        "QA": "Quality",
    }
    cleaned = label
    for technical, business in replacements.items():
        cleaned = re.sub(rf"\b{re.escape(technical)}\b", business, cleaned)
    return cleaned


def project_type_label(mode: str) -> str:
    return {
        "simple_prototype": "Simple prototype",
        "internal_tool": "Internal tool",
        "platform_improvement": "Platform improvement",
        "ui_web_app": "UI/web app",
        "public_demo": "Showcase",
    }.get(mode, mode.replace("_", " ").title())


def scope_size_label(complexity: str) -> str:
    return {
        "simple": "Small",
        "medium": "Medium",
        "complex": "Large",
    }.get(complexity, complexity.title())


def agent_icon_path(agent_name: str) -> str:
    filename = AGENT_ICON_FILES.get(agent_name, "coordinator.png")
    return f"/static/agents/{filename}"


def canonical_artifacts_for_run(
    _run_dir: Path,
    records: Sequence[Any],
    work_items: Sequence[Any] = (),
) -> tuple[list[ArtifactView], list[ArtifactView]]:
    """Build artifact views from Artifact Registry records only."""

    business: list[ArtifactView] = []
    technical: list[ArtifactView] = []
    task_titles: dict[str, str] = {
        str(getattr(item, "work_item_id", "")): str(getattr(item, "title", ""))
        for item in work_items
    }
    task_sprints: dict[str, str] = {
        str(getattr(item, "work_item_id", "")): str(getattr(item, "sprint_id", ""))
        for item in work_items
    }
    for record in records:
        task_id = str(record.work_item_id or "")
        task_titles.setdefault(task_id, "")
        task_sprints.setdefault(task_id, _sprint_for_artifact_task(record))
        if not is_user_facing_artifact_record(record):
            technical.append(_artifact_view_from_record(record, task_titles, task_sprints))
            continue
        business.append(_artifact_view_from_record(record, task_titles, task_sprints))
    return sorted(business, key=_artifact_sort_key), sorted(technical, key=_artifact_sort_key)


def board_cards_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any] = (),
    *,
    open_duration_end_at: str = "",
) -> dict[str, list[BoardCard]]:
    groups: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
    timing_by_item = _card_timing_by_activity_events(
        activity_events,
        open_duration_end_at=open_duration_end_at,
    )
    for item in work_items:
        item_id = _canonical_task_id(str(getattr(item, "work_item_id", "")))
        card = _board_card_from_work_item(item, artifacts, timing_by_item.get(item_id))
        groups.setdefault(card.column, []).append(card)
    for column, cards in groups.items():
        groups[column] = sorted(
            cards,
            key=lambda card: (card.sprint != "Planning", card.order, card.id),
        )
    return groups


def board_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any] = (),
    *,
    open_duration_end_at: str = "",
) -> dict[str, list[BoardCard]]:
    unsorted: dict[str, list[BoardCard]] = {}
    timing_by_item = _card_timing_by_activity_events(
        activity_events,
        open_duration_end_at=open_duration_end_at,
    )
    for item in work_items:
        item_id = _canonical_task_id(str(getattr(item, "work_item_id", "")))
        card = _board_card_from_work_item(item, artifacts, timing_by_item.get(item_id))
        unsorted.setdefault(sprint_label(card.sprint or "Planning"), []).append(card)
    return {
        sprint: sorted(cards, key=lambda card: (card.order, card.id))
        for sprint, cards in sorted(unsorted.items(), key=lambda item: _sprint_sort_key(item[0]))
    }


def sprint_board_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any] = (),
    *,
    open_duration_end_at: str = "",
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for sprint, cards in board_groups_from_work_items(
        work_items,
        artifacts,
        activity_events,
        open_duration_end_at=open_duration_end_at,
    ).items():
        columns: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
        for card in cards:
            columns.setdefault(card.column, []).append(card)
        groups.append({"sprint": sprint, "count": len(cards), "columns": columns})
    return groups


def work_plan_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any] = (),
    *,
    open_duration_end_at: str = "",
) -> list[dict[str, object]]:
    return [
        {"name": sprint, "count": len(cards), "cards": cards}
        for sprint, cards in board_groups_from_work_items(
            work_items,
            artifacts,
            activity_events,
            open_duration_end_at=open_duration_end_at,
        ).items()
    ]


def task_report_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any] = (),
    *,
    open_duration_end_at: str = "",
) -> list[dict[str, object]]:
    return [
        {
            "sprint": sprint,
            "tasks": [
                {
                    "card": card,
                    "reports": _reports_for_card(card, list(artifacts)),
                    "count": len(_reports_for_card(card, list(artifacts))),
                }
                for card in cards
            ],
            "count": len(cards),
        }
        for sprint, cards in board_groups_from_work_items(
            work_items,
            artifacts,
            activity_events,
            open_duration_end_at=open_duration_end_at,
        ).items()
    ]


def activity_groups_from_db_events(
    activity_events: Sequence[Any],
    *,
    task_id: str = "",
) -> list[dict[str, object]]:
    task_filter = _canonical_task_id(task_id.strip()) if task_id else ""
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for event in sorted(
        activity_events,
        key=lambda value: (
            str(getattr(value, "created_at", "") or ""),
            int(getattr(value, "id", 0) or 0),
        ),
    ):
        event_task_id = _canonical_task_id(str(getattr(event, "work_item_id", "")))
        if task_filter and event_task_id != task_filter:
            continue
        message = _business_log_text(str(getattr(event, "message", "")).strip())
        if not message:
            continue
        owner = business_agent_label(
            str(getattr(event, "owner_agent", "") or getattr(event, "agent_id", "") or "Delivery")
        )
        dedupe_key = (owner, _activity_dedupe_text(message))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        timestamp = _display_timestamp(str(getattr(event, "created_at", "") or ""))
        line = f"**{timestamp} - {owner}**\n\n{message}" if timestamp else message
        grouped.setdefault(owner, []).append(render_markdown(line))
    return _ordered_activity_groups(grouped)


def rendered_log_entries_from_db_events(
    activity_events: Sequence[Any],
    *,
    task_id: str = "",
) -> list[str]:
    logs: list[str] = []
    for group in activity_groups_from_db_events(activity_events, task_id=task_id):
        logs.extend(str(log) for log in group["logs"])
    return logs[-180:]


def task_detail_from_work_items(
    task_id: str,
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    activity_events: Sequence[Any],
    *,
    open_duration_end_at: str = "",
) -> TaskDetail | None:
    canonical_task_id = _canonical_task_id(task_id.strip())
    cards = [
        card
        for group in board_groups_from_work_items(
            work_items,
            artifacts,
            activity_events,
            open_duration_end_at=open_duration_end_at,
        ).values()
        for card in group
    ]
    card = next((candidate for candidate in cards if candidate.id == canonical_task_id), None)
    if card is None:
        return None
    return TaskDetail(
        card=card,
        reports=_reports_for_card(card, list(artifacts)),
        logs=rendered_log_entries_from_db_events(activity_events, task_id=canonical_task_id)[-40:],
        activity_groups=activity_groups_from_db_events(
            activity_events,
            task_id=canonical_task_id,
        ),
    )


def delivery_overview_from_work_items(
    *,
    run_id: str,
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    status: str,
    published_url: str = "",
) -> DeliveryOverview:
    cards = [
        _board_card_from_work_item(
            item,
            artifacts,
            _card_timing_by_activity_events(()).get(
                _canonical_task_id(str(getattr(item, "work_item_id", "")))
            ),
        )
        for item in sorted(
            work_items,
            key=lambda value: (
                str(getattr(value, "sprint_id", "") or "planning") != "planning",
                int(getattr(value, "delivery_order", 0) or 0),
                str(getattr(value, "work_item_id", "")),
            ),
        )
    ]
    features = [
        FeatureProgress(
            work_item_id=card.id,
            title=card.title,
            status=_feature_status_from_card(card),
            delivery_order=card.order,
            active=card.active,
            repair_attempts=0,
            owner=card.owner,
            sprint_id=card.sprint,
            lane=card.column,
            assigned_agent=card.owner,
            artifact_count=card.artifact_count,
        )
        for card in cards
    ]
    blockers = [
        str(getattr(item, "blocker", "") or "").strip()
        for item in work_items
        if str(getattr(item, "status", "") or "").lower() == "blocked"
        and str(getattr(item, "blocker", "") or "").strip()
    ]
    normalized_status = status or "running"
    if blockers:
        stage = "blocked"
    elif work_items and all(
        str(getattr(item, "status", "") or "").lower() == "done" for item in work_items
    ):
        stage = "complete"
    elif any(bool(getattr(item, "active", False)) for item in work_items):
        stage = "running"
    else:
        stage = normalized_status
    qa_status = (
        "failed"
        if blockers
        else "passed"
        if work_items
        and all(str(getattr(item, "status", "") or "").lower() == "done" for item in work_items)
        else "review"
        if any(str(getattr(item, "status", "") or "").lower() == "review" for item in work_items)
        else "pending"
    )
    return DeliveryOverview(
        run_id=run_id,
        stage=stage,
        status=normalized_status,
        active_work_item_id=next((card.id for card in cards if card.active), None),
        features=features,
        qa_status=qa_status,
        deployment_status="pending",
        handoff_status="pending",
        topology_summary="",
        deployment_targets=(
            [DeploymentTarget(label="Open App", url=published_url, service="generated-app")]
            if published_url
            else []
        ),
        blockers=blockers[-5:],
        team_lead_steps=[],
        current_work=None,
    )


def run_timing_from_work_items(
    work_items: Sequence[Any],
    activity_events: Sequence[Any],
    *,
    completed: bool,
) -> dict[str, str]:
    timestamps = [
        str(value or "")
        for item in work_items
        for value in (getattr(item, "created_at", ""), getattr(item, "updated_at", ""))
        if str(value or "")
    ]
    timestamps.extend(
        str(getattr(event, "created_at", "") or "")
        for event in activity_events
        if str(getattr(event, "created_at", "") or "")
    )
    if not timestamps:
        return {}
    start = min(timestamps)
    terminal = [
        str(getattr(item, "updated_at", "") or "")
        for item in work_items
        if str(getattr(item, "status", "") or "").lower() in {"done", "blocked"}
        and str(getattr(item, "updated_at", "") or "")
    ]
    end = max(terminal or timestamps) if completed else ""
    return _timing_payload(start, end)


def _board_card_from_work_item(
    item: Any,
    artifacts: Sequence[ArtifactView],
    timing_override: dict[str, str] | None = None,
) -> BoardCard:
    item_id = _canonical_task_id(str(getattr(item, "work_item_id", "")))
    sprint = str(getattr(item, "sprint_id", "") or "sprint-01")
    if sprint.lower() == "planning":
        sprint = "Planning"
    status = str(getattr(item, "status", "") or "todo")
    lane = str(getattr(item, "lane", "") or _board_column("", status))
    artifact_count = sum(1 for artifact in artifacts if artifact.task_id == item_id)
    if timing_override is not None:
        timing = timing_override
    else:
        started_at = "" if lane == "todo" else str(getattr(item, "created_at", "") or "")
        completed_at = (
            str(getattr(item, "updated_at", "") or "") if lane in {"done", "blocked"} else ""
        )
        timing = _timing_payload(started_at, completed_at) if started_at else {}
    return BoardCard(
        id=item_id,
        title=str(getattr(item, "title", "") or item_id),
        owner=business_agent_label(str(getattr(item, "owner_agent", "") or "")),
        sprint=sprint,
        status=work_status_label(status),
        column=(
            lane if lane in {column for column, _ in BOARD_COLUMNS} else _board_column(lane, status)
        ),
        artifact_count=artifact_count,
        active=bool(getattr(item, "active", False)),
        order=int(getattr(item, "delivery_order", 0) or 0),
        started_at=timing.get("started_at", ""),
        completed_at=timing.get("completed_at", ""),
        elapsed_label=timing.get("elapsed_label", ""),
    )


def _feature_status_from_card(card: BoardCard) -> str:
    if card.column == "done":
        return "done"
    if card.column == "blocked":
        return "blocked"
    if card.column == "review":
        return "qa_passed" if "pass" in card.status.lower() else "review"
    if card.column == "in_progress":
        return "in_progress"
    return "pending"


def _artifact_view_from_record(
    record: Any,
    task_titles: dict[str, str],
    task_sprints: dict[str, str],
) -> ArtifactView:
    task_id = str(record.work_item_id or "")
    return ArtifactView(
        path=record.relative_path,
        label=user_friendly_artifact_label_for_record(record),
        agent=record.owner_agent,
        business_agent=business_agent_label(record.owner_agent),
        kind=artifact_kind(record.relative_path),
        technical=not is_user_facing_artifact_record(record),
        phase=task_sprints.get(task_id, ""),
        task_id=task_id,
        task_title=task_titles.get(task_id, ""),
        artifact_id=record.artifact_id,
        visibility=record.visibility,
        artifact_type=record.artifact_type,
    )


def is_user_facing_artifact_record(record: Any) -> bool:
    """Return whether an ArtifactRecord is safe and useful for product UI."""

    normalized = str(record.relative_path).replace("\\", "/")
    filename = Path(normalized).name
    parts = set(Path(normalized).parts)
    if record.visibility not in USER_FACING_VISIBILITIES:
        return False
    if record.artifact_type not in USER_FACING_ARTIFACT_TYPES:
        return False
    if filename in INTERNAL_ARTIFACT_FILENAMES or parts & INTERNAL_ARTIFACT_PATH_PARTS:
        return False
    if normalized.startswith("delivery/"):
        return False
    if normalized.startswith("generated-project/"):
        return False
    if filename.endswith((".jsonl", ".log", ".lock", ".toml")):
        return False
    return True


def user_friendly_artifact_label_for_record(record: Any) -> str:
    type_label = {
        "requirements_brief": "Requirements brief",
        "architecture_report": "Solution overview",
        "delivery_plan": "Delivery plan",
        "execution_summary": "Build summary",
        "qa_report": "Quality summary",
        "repair_request": "Fix request",
        "deployment_summary": "Deployment summary",
        "release_report": "Final report",
        "screenshot_evidence": "Screenshot evidence",
    }.get(str(record.artifact_type), "")
    if type_label:
        task_id = str(record.work_item_id or "")
        if task_id and task_id not in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04", "DEPLOY"}:
            return f"{type_label} - {task_id}"
        return type_label
    return str(record.label or Path(str(record.relative_path)).name)


def artifact_owner_groups(artifacts: list[ArtifactView]) -> list[dict[str, object]]:
    grouped: dict[str, list[ArtifactView]] = {}
    for artifact in artifacts:
        grouped.setdefault(artifact.business_agent, []).append(artifact)
    return [
        {"owner": owner, "artifacts": grouped[owner], "count": len(grouped[owner])}
        for owner in sorted(grouped)
    ]


def artifact_phase_groups(artifacts: list[ArtifactView]) -> list[dict[str, object]]:
    grouped: dict[str, list[ArtifactView]] = {}
    for artifact in artifacts:
        grouped.setdefault(sprint_label(artifact.phase), []).append(artifact)
    ordered = [phase for phase in _PHASE_ORDER if phase in grouped]
    ordered.extend(sorted(phase for phase in grouped if phase not in _PHASE_ORDER))
    return [
        {"phase": phase, "artifacts": grouped[phase], "count": len(grouped[phase])}
        for phase in ordered
    ]


def sprint_label(value: str) -> str:
    token = (value or "Planning").strip()
    match = re.fullmatch(r"sprint-(\d+)", token, flags=re.IGNORECASE)
    if match:
        return f"Sprint {int(match.group(1))}"
    return status_label(token) if "_" in token else token.replace("-", " ").title()


def _sprint_sort_key(value: str) -> tuple[int, int, str]:
    if value == "Planning":
        return (0, 0, value)
    match = re.fullmatch(r"Sprint (\d+)", value, flags=re.IGNORECASE)
    if match:
        return (1, int(match.group(1)), value)
    return (2, 0, value)


def _activity_dedupe_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip().lower()


def _sprint_for_artifact_task(record: Any) -> str:
    metadata = getattr(record, "metadata", {}) or {}
    if isinstance(metadata, dict) and metadata.get("sprint_id"):
        return str(metadata["sprint_id"])
    return ""


def _timing_payload(start: str, end: str = "") -> dict[str, str]:
    return {
        "started_at": _browser_timestamp(start),
        "completed_at": _browser_timestamp(end),
        "elapsed_label": _elapsed_label(start, end),
    }


def _card_timing_by_activity_events(
    activity_events: Sequence[Any],
    *,
    open_duration_end_at: str = "",
) -> dict[str, dict[str, str]]:
    by_item: dict[str, list[Any]] = {}
    for event in activity_events:
        item_id = _canonical_task_id(str(getattr(event, "work_item_id", "") or ""))
        if not item_id:
            continue
        by_item.setdefault(item_id, []).append(event)
    timings: dict[str, dict[str, str]] = {}
    for item_id, events in by_item.items():
        ordered = sorted(events, key=lambda event: str(getattr(event, "created_at", "") or ""))
        start = next(
            (
                str(getattr(event, "created_at", "") or "")
                for event in ordered
                if str(getattr(event, "created_at", "") or "")
            ),
            "",
        )
        terminal = [
            str(getattr(event, "created_at", "") or "")
            for event in ordered
            if _normalize_card_event_status(str(getattr(event, "status", "") or ""))
            in {"done", "blocked"}
            and str(getattr(event, "created_at", "") or "")
        ]
        if start:
            timings[item_id] = _timing_payload(
                start,
                terminal[-1] if terminal else open_duration_end_at,
            )
    return timings


def _normalize_card_event_status(status: str) -> str:
    token = status.strip().lower()
    if any(value in token for value in ("blocked", "failed", "repair")):
        return "blocked"
    if any(value in token for value in ("completed", "passed", "ready", "done", "deployed")):
        return "done"
    if any(value in token for value in ("qa", "review", "inspect", "implemented")):
        return "review"
    if any(value in token for value in ("started", "running", "progress")):
        return "in_progress"
    return token


def _elapsed_label(start: str, end: str = "") -> str:
    started = _parse_timestamp(start)
    finished = _parse_timestamp(end) if end else datetime.now(UTC)
    if not started:
        return ""
    seconds = max(0, int((finished - started).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remainder = minutes % 60
    return f"{hours}h {remainder}m" if remainder else f"{hours}h"


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone().astimezone(UTC)
    return parsed.astimezone(UTC)


def _browser_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _display_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _is_work_item_id(value: str) -> bool:
    return bool(re.fullmatch(r"(F\d+|US-[A-Za-z0-9][A-Za-z0-9_-]*|DEPLOY)", value, re.IGNORECASE))


def _reports_for_card(
    card: BoardCard,
    artifacts: list[ArtifactView],
) -> list[ArtifactView]:
    matches = [artifact for artifact in artifacts if _artifact_matches_card(artifact, card)]
    return sorted(matches, key=_artifact_sort_key)


def _artifact_matches_card(
    artifact: ArtifactView,
    card: BoardCard,
) -> bool:
    return bool(artifact.task_id and artifact.task_id == card.id)


def _canonical_task_id(task_id: str) -> str:
    return task_id.strip()


def _ordered_activity_groups(
    grouped: dict[str, list[str]],
    *,
    preferred: list[str] | None = None,
) -> list[dict[str, object]]:
    ordered = preferred or [
        "Coordinator",
        "Business Analyst",
        "Solution Architect",
        "Delivery Planner",
        "Delivery Lead",
        "Builder",
        "Quality Reviewer",
        "Publisher",
        "Release Reporter",
        "Delivery",
    ]
    owner_order = {owner: index for index, owner in enumerate(ordered)}
    return [
        {"owner": owner, "logs": logs, "count": len(logs)}
        for owner, logs in sorted(
            grouped.items(),
            key=lambda item: (owner_order.get(item[0], 99), item[0]),
        )
    ]


def agent_catalog(
    *,
    agent_provider: str = "google_gemini",
    agent_model: str = "gemini-3.1-flash-lite",
    codex_model: str = DEFAULT_CODEX_MODEL,
    codex_reasoning: str = "medium",
) -> list[dict[str, str]]:
    planning_provider = "Google Gemini" if agent_provider == "google_gemini" else "OpenAI"
    agents = [
        {
            "name": "Coordinator",
            "initials": "CO",
            "detail": "Plans the delivery journey and delegates work.",
            "model": agent_model,
            "reasoning": "none",
            "provider": planning_provider,
        },
        {
            "name": "Business Analyst",
            "initials": "BA",
            "detail": "Clarifies scope, users, and acceptance criteria.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Solution Architect",
            "initials": "SA",
            "detail": "Shapes the solution approach and diagrams.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Delivery Planner",
            "initials": "DP",
            "detail": "Builds sprint plan, sequencing, and release path.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Delivery Lead",
            "initials": "DL",
            "detail": "Coordinates build, quality review, publishing, and reporting.",
            "model": agent_model,
            "reasoning": "none",
            "provider": planning_provider,
        },
        {
            "name": "Builder",
            "initials": "B",
            "detail": "Creates the application slice by slice.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Quality Reviewer",
            "initials": "QR",
            "detail": "Checks product behavior, design quality, and release confidence.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Publisher",
            "initials": "P",
            "detail": "Packages and publishes the generated app.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
        {
            "name": "Release Reporter",
            "initials": "RP",
            "detail": "Prepares the business-facing final report.",
            "model": codex_model,
            "reasoning": codex_reasoning,
            "provider": "Build engine",
        },
    ]
    return [{**agent, "icon": agent_icon_path(agent["name"])} for agent in agents]


def artifact_payload_for_record(record: Any, content: Any | None) -> dict[str, Any]:
    if record is None or not is_user_facing_artifact_record(record):
        raise ValueError("Artifact is not user-facing")
    if content is None:
        raise ValueError("Artifact content is not registered in DB")
    kind = artifact_kind(record.relative_path)
    content_kind = str(content.get("content_kind") or "")
    content_text = str(content.get("content_text") or "")
    content_json = content.get("content_json")
    if kind == "json":
        if content_kind == "json":
            return {"kind": kind, "content": json.dumps(content_json, indent=2)}
        return {"kind": "text", "content": content_text}
    if kind == "csv":
        rows = list(csv.reader(content_text.splitlines()))
        return {"kind": kind, "rows": rows}
    if kind == "markdown":
        return {"kind": kind, "content": render_markdown(content_text)}
    if kind == "html":
        return {"kind": kind, "content": content_text}
    if kind == "mermaid":
        return {"kind": kind, "content": _normalize_mermaid(content_text)}
    return {"kind": kind, "content": content_text}


def html_report_document(content: str) -> str:
    """Return an HTML report with links/forms escaping the embedded preview."""

    base = '<base target="_blank">'
    if re.search(r"<base\b", content, flags=re.IGNORECASE):
        return content
    head_match = re.search(r"<head\b[^>]*>", content, flags=re.IGNORECASE)
    if head_match:
        return content[: head_match.end()] + base + content[head_match.end() :]
    html_match = re.search(r"<html\b[^>]*>", content, flags=re.IGNORECASE)
    if html_match:
        return (
            content[: html_match.end()] + "<head>" + base + "</head>" + content[html_match.end() :]
        )
    return f"<!doctype html><html><head>{base}</head><body>{content}</body></html>"


def _normalize_mermaid(content: str) -> str:
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:mermaid)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    return cleaned.replace(r"\n", "<br/>")


def artifact_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".html":
        return "html"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    if suffix == ".mmd":
        return "mermaid"
    return "text"


_PHASE_ORDER = (
    "Requirements",
    "Solution Design",
    "Delivery Planning",
    "Sprint 1",
    "Sprint 2",
    "Sprint 3",
    "Sprint 4",
    "Sprint 5",
    "Final Report",
)


def _artifact_sort_key(artifact: ArtifactView) -> tuple[int, str, str, str]:
    phase_index = _PHASE_ORDER.index(artifact.phase) if artifact.phase in _PHASE_ORDER else 99
    return (phase_index, artifact.task_id or "zzzz", artifact.business_agent, artifact.label)


def render_markdown(text: str) -> str:
    """Render enough Markdown for business artifacts without adding a dependency."""

    text = _plain_markdown_links(text)
    blocks = _extract_fenced_blocks(text)
    escaped = html.escape(blocks["text"])
    escaped = _restore_fenced_blocks(escaped, blocks["blocks"])
    escaped = re.sub(r"^### (.+)$", r"<h3>\1</h3>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^## (.+)$", r"<h2>\1</h2>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^# (.+)$", r"<h1>\1</h1>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    lines = []
    in_list = False
    table_buffer: list[str] = []

    def flush_table() -> None:
        nonlocal table_buffer
        if table_buffer:
            lines.append(_render_markdown_table(table_buffer))
            table_buffer = []

    for line in escaped.splitlines():
        if _looks_like_table_row(line):
            if in_list:
                lines.append("</ul>")
                in_list = False
            table_buffer.append(line)
            continue
        flush_table()
        if line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            lines.append("</ul>")
            in_list = False
        if line.startswith("<h"):
            lines.append(line)
        elif line.strip() == "&gt;":
            continue
        elif line.startswith("&gt; "):
            quote = line[5:].strip()
            if quote:
                lines.append(f"<blockquote>{quote}</blockquote>")
        elif " &gt; " in line:
            heading, quote = line.split(" &gt; ", 1)
            if heading.strip():
                lines.append(f"<p>{heading}</p>")
            if quote.strip():
                lines.append(f"<blockquote>{quote}</blockquote>")
        elif line.strip():
            lines.append(f"<p>{line}</p>")
    if in_list:
        lines.append("</ul>")
    flush_table()
    return "\n".join(lines)


def _extract_fenced_blocks(text: str) -> dict[str, Any]:
    blocks: list[tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        language = (match.group(1) or "").strip().lower()
        content = match.group(2)
        token = f"@@FENCED_BLOCK_{len(blocks)}@@"
        if language == "mermaid":
            rendered = f'<pre class="mermaid">{html.escape(_normalize_mermaid(content))}</pre>'
        else:
            class_attr = f' class="language-{html.escape(language)}"' if language else ""
            rendered = f"<pre><code{class_attr}>{html.escape(content.rstrip())}</code></pre>"
        blocks.append((token, rendered))
        return token

    cleaned = re.sub(r"```([A-Za-z0-9_-]*)\s*\n(.*?)```", replace, text, flags=re.DOTALL)
    return {"text": cleaned, "blocks": blocks}


def _restore_fenced_blocks(text: str, blocks: list[tuple[str, str]]) -> str:
    restored = text
    for token, rendered in blocks:
        restored = restored.replace(token, rendered)
    return restored


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _render_markdown_table(rows: list[str]) -> str:
    parsed = [
        [cell.strip() for cell in row.strip().strip("|").split("|")]
        for row in rows
        if _looks_like_table_row(row)
    ]
    if len(parsed) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in parsed[1]):
        header, body = parsed[0], parsed[2:]
    else:
        header, body = [], parsed
    html_rows: list[str] = ['<div class="table-scroll"><table>']
    if header:
        html_rows.append("<thead><tr>")
        html_rows.extend(f"<th>{cell}</th>" for cell in header)
        html_rows.append("</tr></thead>")
    if body:
        html_rows.append("<tbody>")
        for row in body:
            html_rows.append("<tr>")
            html_rows.extend(f"<td>{cell}</td>" for cell in row)
            html_rows.append("</tr>")
        html_rows.append("</tbody>")
    html_rows.append("</table></div>")
    return "".join(html_rows)


def _plain_markdown_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"`\1`", text)


def system_checks(
    openai_key_configured: bool | None = None,
    gemini_key_configured: bool | None = None,
) -> list[dict[str, str]]:
    if openai_key_configured is None:
        openai_key_configured = bool(os.getenv("OPENAI_API_KEY") or os.getenv("CODEX_API_KEY"))
    if gemini_key_configured is None:
        gemini_key_configured = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    codex_available = bool(_codex_binary())
    docker_available = bool(shutil.which("docker"))
    azure_available = bool(shutil.which("az"))
    internet_available = _internet_available()
    return [
        _check("Project history", True, "Projects can be saved."),
        _check(
            "OpenAI key",
            openai_key_configured,
            "Ready to start projects."
            if openai_key_configured
            else "Add your key in Settings before starting.",
        ),
        _check(
            "Gemini key",
            gemini_key_configured,
            "Gemini can power agent planning."
            if gemini_key_configured
            else "Add a Gemini key to use Google models.",
        ),
        _check(
            "Builder",
            codex_available,
            "Builder is available." if codex_available else "Builder is not available.",
        ),
        _check(
            "App packaging",
            docker_available,
            "Packaging tools are available."
            if docker_available
            else "Packaging tools are not available.",
        ),
        _check(
            "Cloud tools",
            azure_available,
            "Cloud deployment tools are available."
            if azure_available
            else "Cloud deployment tools are not available.",
        ),
        _check(
            "Internet",
            internet_available,
            "Internet access is available."
            if internet_available
            else "Internet access could not be confirmed.",
        ),
    ]


def user_facing_blockers(blockers: list[str]) -> list[str]:
    return [_user_facing_blocker(blocker) for blocker in blockers if str(blocker).strip()]


def _user_facing_blocker(blocker: str) -> str:
    text = str(blocker).strip()
    lowered = text.lower()
    if "reasoning_effort" in lowered and "unrecognized request argument" in lowered:
        return (
            "Coordinator could not start because the selected model settings were "
            "incompatible. The project can be restarted after updating Settings."
        )
    if "openai_api_key is required" in lowered:
        return "Add your OpenAI key in Settings before starting this project."
    if "agentexecutor failed" in lowered or "executor failed" in lowered:
        return (
            "The delivery team could not complete this step. Review Settings and restart "
            "the project."
        )
    if "error code:" in lowered or "{'error':" in lowered or '"error"' in lowered:
        return "A service error interrupted this step. Review Settings and restart the project."
    cleaned = _business_log_text(text)
    cleaned = re.sub(r"\s*\{.*\}\s*$", "", cleaned).strip()
    return cleaned or "This step needs attention before the project can continue."


def _business_log_text(entry: str) -> str:
    replacements = {
        "Head Agent": "Coordinator",
        "Business Analyst": "Business Analyst",
        "Business Analysis": "Requirements",
        "Solution Architecture": "Solution approach",
        "Architect": "Solution Architect",
        "Project Manager": "Delivery Planner",
        "Team Lead": "Delivery Lead",
        "Fullstack Agent": "Builder",
        "QA Agent": "Quality Reviewer",
        "QA Codex": "Quality Reviewer",
        "QA": "Quality",
        "Deployment Agent": "Publisher",
        "Deployment Codex": "Publisher",
        "Deployment": "Publishing",
        "Handoff Agent": "Release Reporter",
        "Handoff Codex": "Release Reporter",
        "Handoff": "Release Report",
        "Head Codex Review": "Coordinator Quality Review",
        "Team Lead Codex Review": "Delivery Lead Quality Review",
        "delivery-graph": "Delivery workflow",
        "head-agent": "Coordinator",
        "business-analyst-agent": "Business Analyst",
        "architect-agent": "Solution Architect",
        "project-manager-agent": "Delivery Planner",
        "team-lead-agent": "Delivery Lead",
        "fullstack-agent": "Builder",
        "qa-agent": "Quality Reviewer",
        "qa-codex-agent": "Quality Reviewer",
        "deployment-agent": "Publisher",
        "deployment-codex-agent": "Publisher",
        "documentation-handoff-agent": "Release Reporter",
        "handoff-codex-agent": "Release Reporter",
        "Graph state saved": "Progress saved",
        "Delivery graph": "Delivery workflow",
        "Graph node": "Workflow step",
        " tool ": " work ",
    }
    cleaned = entry
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(
        r"\b(Business Analyst|Solution Architect|Delivery Planner|Quality Reviewer|"
        r"Publisher|Release Reporter|Coordinator Quality Review|"
        r"Delivery Lead Quality Review) Codex\b",
        r"\1",
        cleaned,
    )
    cleaned = re.sub(r"\bCodex(\s+\([^)]+\))", r"Builder\1", cleaned)
    cleaned = re.sub(r"\bCodex\b", "Builder", cleaned)
    cleaned = re.sub(r"\s\|\s(tool|node|target|reason|status)=[^\n]+", "", cleaned)
    cleaned = _sanitize_activity_paths(cleaned)
    return cleaned


def _sanitize_activity_paths(text: str) -> str:
    cleaned = _plain_markdown_links(text)
    cleaned = cleaned.replace("`upstream-planning`", "planning")
    cleaned = cleaned.replace("upstream-planning", "planning")
    technical_suffixes = (
        "json",
        "jsonl",
        "md",
        "html",
        "csv",
        "log",
        "txt",
        "py",
        "js",
        "css",
        "toml",
        "lock",
        "example",
        "mmd",
        "db",
        "env",
    )
    suffix_pattern = "|".join(technical_suffixes)
    cleaned = re.sub(
        rf"^\s*>?\s*-\s*`?[^`\n]*(?:\.({suffix_pattern})|Dockerfile|README)`?\s*$",
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    cleaned = re.sub(
        r"^\s*>?\s*\d+\.\s*`?[^`\n]*\.(?:" + suffix_pattern + r")`?\s*$",
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    cleaned = re.sub(
        r"`(?:[^`\n]*[/\\])?[^`\n]*\.(?:" + suffix_pattern + r")`",
        "project materials",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:Created/updated|Created all required .*?artifacts|"
        r"Created all requested .*?artifacts|Updated artifacts):\s*$",
        "Updated the relevant project materials.",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(
        r"\s+and wrote (?:the )?required artifacts",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:required artifacts|Quality JSON and Markdown artifacts|"
        r"Quality JSON and Markdown report)\b",
        "project materials",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"all (?:required artifacts|project materials) were refreshed at the contract paths",
        "the release materials were refreshed",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+to (?:the )?exact contract paths?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*\(?BOM-free UTF-8\)?|\s+without BOM|\s+no-BOM",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*>?\s*-\s*.*(?:byte check|JSON parse|valid JSON|parseable|BOM).*?$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(
        r"`(?:app-agentic[^`]*|rg-agentic[^`]*|agenticdev[^`]*|agentic-chat[^`]*)`",
        "cloud resource",
        cleaned,
    )
    cleaned = re.sub(
        r"^\s*>?\s*-\s*.*(?:uv run|GET /|POST /|DATABASE_URL|TASK_STORE_PATH|"
        r"azurecr\.io|azurecontainerapps\.io|rg-agentic|agenticdev|app-agentic).*?$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(
        r"^\s*>?\s*-\s*(?:Built and pushed|Reused existing|Updated cloud resource|"
        r"new image|runtime env wiring|Verified health|Ran post-deploy|"
        r"Public Quality target).*?$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(
        rf"\s\|\s(?:[\w.-]+/)+[\w.-]+\.(?:{suffix_pattern})",
        "",
        cleaned,
    )
    cleaned = re.sub(r"https?://\S+", "the published app link", cleaned)
    cleaned = re.sub(
        r"\(([A-Za-z]:\\[^)\n]+|/[A-Za-z0-9_.\-/]+)\)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\b[A-Za-z]:\\[^\s)\]]+", "", cleaned)
    cleaned = re.sub(
        r"(?m)^\s*>?\s*(?:Canonical artifact refs|Contract checks|Updated artifacts):.*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"^\s*>?\s*-\s*`?\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"`\s*`", "", cleaned)
    cleaned = re.sub(
        r"(?:project materials,\s*){2,}project materials",
        "project materials",
        cleaned,
    )
    cleaned = re.sub(
        r"(?:project materials\s*(?:and|,)\s*)+project materials",
        "project materials",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def format_request_text(text: str) -> str:
    """Structure dictated text while preserving the original user input."""

    original = _normalize_speech_text(text)
    if not original:
        return ""

    cleaned = _apply_common_speech_fixes(_drop_leading_greeting(original))
    requirements = _structured_requirements(cleaned)
    if not requirements:
        requirements = _requirement_summary_bullets(cleaned)

    lines = [
        "# Product Request",
        "",
        "## Summary",
        _summary_sentence(cleaned, requirements),
        "",
        "## Requirements",
        *[f"- {requirement}" for requirement in requirements],
        "",
        "## Notes",
        "- Preserve the requested behavior and keep the implementation simple.",
        "- Do not add extra product scope beyond the request.",
    ]
    return "\n".join(lines).strip() + "\n"


def format_request_text_with_llm(text: str, *, api_key: str = "") -> str:
    """Use Gemini to structure a request."""

    clean = _normalize_speech_text(text)
    if not clean:
        return ""
    key = api_key.strip()
    if not key:
        raise GeminiFormatterUnavailable("Gemini is not configured.")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise GeminiFormatterUnavailable("Gemini formatter dependencies are unavailable.") from exc

    prompt = (
        "Format the user's dictated product request into clean Markdown.\n"
        "Preserve 100% of the user's meaning and requested facts.\n"
        "Do not add new requirements, assumptions, technologies, deadlines, or scope.\n"
        "Do not reduce the request to a generic placeholder.\n"
        "Keep all concrete details, quantities, interactions, deliverables, constraints, "
        "acceptance signals, and outcome requests when present.\n"
        "If the text is unclear, preserve the clearest literal intent instead of deleting it.\n"
        "Remove filler greetings, duplicated speech fragments, and transcription noise.\n"
        "Do not include an Original Dictated Text section or raw transcript.\n"
        "Return only the polished request that the user can approve and edit.\n"
        "Use this structure exactly:\n"
        "# Product Request\n\n"
        "## Summary\n"
        "<one concise sentence>\n\n"
        "## Requirements\n"
        "- <bullet>\n\n"
        "## Notes\n"
        "- Preserve the requested behavior and keep the implementation simple.\n"
        "- Do not add extra product scope beyond the request.\n\n"
        f"User text:\n{clean}"
    )
    try:
        response = ChatGoogleGenerativeAI(
            google_api_key=key,
            model=os.getenv(
                "AGENTIC_FORMATTER_MODEL",
                "gemini-3.1-flash-lite",
            ),
            temperature=0,
        ).invoke(prompt)
    except Exception as exc:
        raise GeminiFormatterUnavailable("Gemini is not reachable right now.") from exc
    formatted = _llm_text_content(response.content).strip()
    if not formatted:
        raise GeminiFormatterUnavailable("Gemini returned an empty response.")
    return formatted + "\n"


class GeminiFormatterUnavailable(RuntimeError):
    """Raised when the Gemini-backed request formatter cannot be used."""


def _llm_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part.strip())
    return str(content)


def _normalize_speech_text(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    clean = re.sub(r"\s+([,.!?;:])", r"\1", clean)
    return clean


def _drop_leading_greeting(text: str) -> str:
    return re.sub(r"^(hi|hello|hey)(\s+\1)+[,.!?\s]*", "", text, flags=re.IGNORECASE).strip()


def _apply_common_speech_fixes(text: str) -> str:
    replacements = {
        r"\bstream lead\b": "web app",
        r"\bchalks\b": "charts",
        r"\bpreach and rated\b": "pre-generated",
    }
    clean = text
    for pattern, replacement in replacements.items():
        clean = re.sub(pattern, replacement, clean, flags=re.IGNORECASE)
    return clean[0].upper() + clean[1:] if clean else clean


def _structured_requirements(text: str) -> list[str]:
    lower = text.lower()
    requirements: list[str] = []
    if "web app" in lower or "small app" in lower:
        requirements.append("Build a small web app.")
    if "three buttons" in lower or "3 buttons" in lower:
        requirements.append("Include three clearly visible buttons.")
    if "three possible actions" in lower or "3 possible actions" in lower:
        requirements.append("Support three possible user actions.")
    elif "actions" in lower and "button" in lower:
        requirements.append("Each button should trigger a visible action.")
    if "five different states" in lower or "5 different states" in lower:
        requirements.append("Support about five different app states.")
    if "click" in lower and ("appears" in lower or "show" in lower):
        examples = _examples_from_text(lower)
        requirements.append(
            "When a button is clicked, show a visible result"
            + (f" such as {examples}." if examples else ".")
        )
    if "locally" in lower or "generate it locally" in lower:
        requirements.append("Generate the app locally before deployment.")
    if "deployed" in lower or "deploy" in lower:
        requirements.append("Deploy the finished app.")
    if "report" in lower:
        report = "Provide a small, simple report"
        if "link" in lower:
            report += " with the application link"
        requirements.append(report + ".")
    return _dedupe_preserve_order(requirements)


def _examples_from_text(lower: str) -> str:
    examples = []
    for token, label in (
        ("chart", "charts"),
        ("number", "numbers"),
        ("action", "simple actions"),
    ):
        if token in lower:
            examples.append(label)
    unique = _dedupe_preserve_order(examples)
    if len(unique) <= 1:
        return "".join(unique)
    return ", ".join(unique[:-1]) + f", or {unique[-1]}"


def _requirement_summary_bullets(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _ensure_sentence(text))
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _summary_sentence(text: str, requirements: list[str]) -> str:
    joined = " ".join(requirements).lower()
    if "small web app" in joined and "three clearly visible buttons" in joined:
        return (
            "Build a small web app with three buttons, visible click results, "
            "deployment, and a simple report with the app link."
        )
    first = _requirement_summary_bullets(text)[0]
    return first if len(first) <= 180 else first[:177].rstrip() + "..."


def _ensure_sentence(text: str) -> str:
    clean = text.strip()
    if clean and clean[-1] not in ".!?":
        clean += "."
    return clean


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {
        "name": name,
        "ok": "true" if ok else "false",
        "status": "Ready" if ok else "Needs setup",
        "detail": detail,
    }


def _codex_binary() -> str:
    configured = os.getenv("CODEX_BINARY", "").strip()
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("codex")
    return found or ""


def _internet_available() -> bool:
    try:
        urllib.request.urlopen("https://www.google.com/generate_204", timeout=2).close()
        return True
    except Exception:
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=2).close()
            return True
        except OSError:
            return False


def _board_column(lane: str, status: str) -> str:
    token = f"{lane} {status}".lower()
    if "blocked" in token or "failed" in token:
        return "blocked"
    if any(value in token for value in ("done", "passed", "deployed", "ready", "closed")):
        return "done"
    if "qa" in token or "quality" in token:
        return "qa"
    if "review" in token or "inspect" in token:
        return "qa"
    if any(value in token for value in ("progress", "running", "active", "doing")):
        return "in_progress"
    return "todo"


def root_path() -> Path:
    return repo_root()
