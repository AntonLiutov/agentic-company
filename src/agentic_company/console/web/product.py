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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic_company.console.live_logs import friendly_log_entries
from agentic_company.console.support import (
    DeliveryOverview,
    DeploymentTarget,
    FeatureProgress,
    artifact_groups_for_run,
    console_status_label,
    delivery_overview_for_run,
    execution_completed,
    read_events,
    read_json_artifact,
    read_text_artifact,
    repo_root,
)
from agentic_company.platform.artifact_registry import (
    USER_FACING_VISIBILITIES,
    get_artifact_by_id,
    list_artifacts,
    register_artifact,
)

AGENT_MODEL_OPTIONS = [
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-5.2",
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
    "gpt-5.3-codex",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
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
    "feature_queue_qa_completed_deployment_ready": "Ready for Publishing",
    "feature_queue_qa_completed_downstream_paused": "Quality Complete",
    "fullstack": "Build",
    "fullstack_feature_implemented": "Build Ready",
    "fullstack_feature_queue_completed_downstream_paused": "Build Complete",
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
    "requirements_brief",
    "architecture_report",
    "delivery_plan",
    "execution_summary",
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
    fallback = agent.replace("-agent", "").replace("-", " ").title()
    return AGENT_LABELS.get(agent, normalized.get(agent, fallback))


def status_label(status: str) -> str:
    token = (status or "pending").strip().lower()
    if token in STATUS_LABELS:
        return STATUS_LABELS[token]
    return _remove_internal_names(console_status_label(token))


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


def board_cards_for_run(run_dir: Path) -> dict[str, list[BoardCard]]:
    overview = delivery_overview_for_run(run_dir)
    timings = _card_timings_for_run(run_dir)
    grouped: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
    if overview.features:
        for feature in overview.features:
            column = _board_column(feature.lane or feature.status, feature.status)
            timing = timings.get(feature.feature_id, {})
            grouped.setdefault(column, []).append(
                BoardCard(
                    id=feature.feature_id,
                    title=feature.title or feature.feature_id,
                    owner=business_agent_label(feature.assigned_agent or feature.owner),
                    sprint=feature.sprint_id or "Planning",
                    status=work_status_label(feature.status),
                    column=column,
                    artifact_count=feature.artifact_count,
                    active=feature.active,
                    order=feature.delivery_order,
                    started_at=timing.get("started_at", ""),
                    completed_at=timing.get("completed_at", ""),
                    elapsed_label=timing.get("elapsed_label", ""),
                )
            )
        _apply_role_timing_fallbacks(grouped, run_dir)
        return grouped

    stage = overview.stage or "planning"
    timing = run_timing_for_run(run_dir)
    grouped[_board_column(stage, overview.status)].append(
        BoardCard(
            id=overview.run_id,
            title=f"{status_label(stage)} workflow",
            owner="Coordinator",
            sprint="Current run",
            status=status_label(overview.status),
            column=_board_column(stage, overview.status),
            artifact_count=sum(len(group[2]) for group in artifact_groups_for_run(run_dir)),
            active=True,
            order=0,
            started_at=timing.get("started_at", ""),
            completed_at=timing.get("completed_at", ""),
            elapsed_label=timing.get("elapsed_label", ""),
        )
    )
    return grouped


def artifacts_for_run(run_dir: Path) -> tuple[list[ArtifactView], list[ArtifactView]]:
    _backfill_visible_legacy_artifacts(run_dir)
    registry_artifacts = _registry_artifacts_for_run(run_dir)
    if registry_artifacts[0] or registry_artifacts[1]:
        return registry_artifacts
    return _legacy_artifacts_for_run(run_dir)


def canonical_artifacts_for_run(
    run_dir: Path,
    records: Sequence[Any],
) -> tuple[list[ArtifactView], list[ArtifactView]]:
    """Build artifact views from Artifact Registry records only."""

    business: list[ArtifactView] = []
    technical: list[ArtifactView] = []
    task_titles: dict[str, str] = {}
    task_sprints: dict[str, str] = {}
    for record in records:
        if not _artifact_record_file_exists(run_dir, record):
            continue
        task_id = str(record.work_item_id or _task_id_for_artifact(record.relative_path))
        task_titles.setdefault(task_id, _title_for_artifact_task(task_id))
        task_sprints.setdefault(task_id, _sprint_for_artifact_task(task_id, record))
        if not is_user_facing_artifact_record(record):
            technical.append(_artifact_view_from_record(record, task_titles, task_sprints))
            continue
        business.append(_artifact_view_from_record(record, task_titles, task_sprints))
    return sorted(business, key=_artifact_sort_key), sorted(technical, key=_artifact_sort_key)


def canonical_board_cards_for_run(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> dict[str, list[BoardCard]]:
    """Project board cards from structured tool trace and artifact records."""

    groups: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
    cards = _canonical_cards_from_trace(run_dir, tool_events, artifacts, run_events)
    if not cards:
        return groups
    for card in cards:
        groups.setdefault(card.column, []).append(card)
    for column, column_cards in groups.items():
        groups[column] = sorted(
            column_cards,
            key=lambda card: (card.sprint != "Planning", card.order, card.id),
        )
    return groups


def canonical_board_groups_for_run(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> dict[str, list[BoardCard]]:
    unsorted: dict[str, list[BoardCard]] = {}
    for cards in canonical_board_cards_for_run(
        run_dir, tool_events, artifacts, run_events
    ).values():
        for card in cards:
            unsorted.setdefault(sprint_label(card.sprint or "Planning"), []).append(card)
    return {
        sprint: sorted(cards, key=lambda card: (card.order, card.id))
        for sprint, cards in sorted(unsorted.items(), key=lambda item: _sprint_sort_key(item[0]))
    }


def canonical_sprint_board_groups_for_run(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for sprint, cards in canonical_board_groups_for_run(
        run_dir, tool_events, artifacts, run_events
    ).items():
        columns: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
        for card in cards:
            columns.setdefault(card.column, []).append(card)
        groups.append({"sprint": sprint, "count": len(cards), "columns": columns})
    return groups


def canonical_work_plan_groups_for_run(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> list[dict[str, object]]:
    return [
        {"name": sprint, "count": len(cards), "cards": cards}
        for sprint, cards in canonical_board_groups_for_run(
            run_dir, tool_events, artifacts, run_events
        ).items()
    ]


def board_cards_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
) -> dict[str, list[BoardCard]]:
    groups: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
    for item in work_items:
        card = _board_card_from_work_item(item, artifacts)
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
) -> dict[str, list[BoardCard]]:
    unsorted: dict[str, list[BoardCard]] = {}
    for item in work_items:
        card = _board_card_from_work_item(item, artifacts)
        unsorted.setdefault(sprint_label(card.sprint or "Planning"), []).append(card)
    return {
        sprint: sorted(cards, key=lambda card: (card.order, card.id))
        for sprint, cards in sorted(unsorted.items(), key=lambda item: _sprint_sort_key(item[0]))
    }


def sprint_board_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for sprint, cards in board_groups_from_work_items(work_items, artifacts).items():
        columns: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
        for card in cards:
            columns.setdefault(card.column, []).append(card)
        groups.append({"sprint": sprint, "count": len(cards), "columns": columns})
    return groups


def work_plan_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
) -> list[dict[str, object]]:
    return [
        {"name": sprint, "count": len(cards), "cards": cards}
        for sprint, cards in board_groups_from_work_items(work_items, artifacts).items()
    ]


def task_report_groups_from_work_items(
    work_items: Sequence[Any],
    artifacts: Sequence[ArtifactView],
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
        for sprint, cards in board_groups_from_work_items(work_items, artifacts).items()
    ]


def activity_groups_from_db_events(
    activity_events: Sequence[Any],
    *,
    task_id: str = "",
) -> list[dict[str, object]]:
    task_filter = _canonical_task_id(task_id.strip()) if task_id else ""
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for event in activity_events:
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
        timestamp = _browser_timestamp(str(getattr(event, "created_at", "") or ""))
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
) -> TaskDetail | None:
    canonical_task_id = _canonical_task_id(task_id.strip())
    cards = [
        card
        for group in board_groups_from_work_items(work_items, artifacts).values()
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


def _board_card_from_work_item(
    item: Any,
    artifacts: Sequence[ArtifactView],
) -> BoardCard:
    item_id = _canonical_task_id(str(getattr(item, "work_item_id", "")))
    sprint = str(getattr(item, "sprint_id", "") or "sprint-01")
    if sprint.lower() == "planning":
        sprint = "Planning"
    status = str(getattr(item, "status", "") or "todo")
    lane = str(getattr(item, "lane", "") or _board_column("", status))
    artifact_ids = getattr(item, "artifact_ids", []) or []
    artifact_count = len(artifact_ids) or sum(
        1 for artifact in artifacts if artifact.task_id == item_id
    )
    started_at = "" if lane == "todo" else str(getattr(item, "created_at", "") or "")
    completed_at = str(getattr(item, "updated_at", "") or "") if lane in {"done", "blocked"} else ""
    timing = _timing_payload(started_at, completed_at) if started_at else {}
    return BoardCard(
        id=item_id,
        title=str(getattr(item, "title", "") or item_id),
        owner=business_agent_label(str(getattr(item, "owner_agent", "") or "")),
        sprint=sprint,
        status=work_status_label(status),
        column=(
            lane
            if lane in {column for column, _ in BOARD_COLUMNS}
            else _board_column(lane, status)
        ),
        artifact_count=artifact_count,
        active=bool(getattr(item, "active", False)),
        order=int(getattr(item, "delivery_order", 0) or 0),
        started_at=timing.get("started_at", ""),
        completed_at=timing.get("completed_at", ""),
        elapsed_label=timing.get("elapsed_label", ""),
    )


def canonical_task_report_groups_for_run(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> list[dict[str, object]]:
    return [
        {
            "sprint": sprint,
            "tasks": [
                {
                    "card": card,
                    "reports": _reports_for_card(card, artifacts),
                    "count": len(_reports_for_card(card, artifacts)),
                }
                for card in cards
            ],
            "count": len(cards),
        }
        for sprint, cards in canonical_board_groups_for_run(
            run_dir, tool_events, artifacts, run_events
        ).items()
    ]


def canonical_activity_groups_for_run(
    tool_events: Sequence[Any],
    *,
    task_id: str = "",
    run_events: Sequence[Any] = (),
) -> list[dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    task_filter = _canonical_task_id(task_id.strip()) if task_id else ""
    payloads = [
        *[_run_event_as_work_event(event) or _event_payload(event) for event in run_events],
        *[_event_payload(event) for event in tool_events],
    ]
    for payload in _sorted_activity_payloads(payloads):
        if _is_developer_only_run_event(payload):
            continue
        work_item_id = str(payload.get("work_item_id") or "")
        if task_filter and _canonical_work_item_id(work_item_id, payload) != task_filter:
            continue
        owner = business_agent_label(str(payload.get("agent_id") or "Delivery"))
        summary = _dashboard_comment_for_event(payload)
        if not summary:
            continue
        dedupe_key = (owner, _activity_dedupe_text(summary))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        timestamp = _browser_timestamp(str(payload.get("created_at") or ""))
        line = f"**{timestamp} - {owner}**\n\n{summary}" if timestamp else summary
        grouped.setdefault(owner, []).append(render_markdown(line))
    return _ordered_activity_groups(grouped)


def canonical_rendered_log_entries_for_run(
    tool_events: Sequence[Any],
    run_events: Sequence[Any] = (),
    *,
    task_id: str = "",
) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    task_filter = _canonical_task_id(task_id.strip()) if task_id else ""
    payloads = [
        *[_run_event_as_work_event(event) or _event_payload(event) for event in run_events],
        *[_event_payload(event) for event in tool_events],
    ]
    for payload in _sorted_activity_payloads(payloads):
        if _is_developer_only_run_event(payload):
            continue
        work_item_id = str(payload.get("work_item_id") or "")
        if task_filter and _canonical_work_item_id(work_item_id, payload) != task_filter:
            continue
        comment = _dashboard_comment_for_event(payload)
        if not comment:
            continue
        dedupe_key = _activity_dedupe_text(comment)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        entries.append(render_markdown(comment))
    return entries[-180:]


def canonical_task_detail_for_run(
    run_dir: Path,
    task_id: str,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> TaskDetail | None:
    task_id = _canonical_task_id(task_id.strip())
    cards = [
        card
        for group in canonical_board_groups_for_run(
            run_dir, tool_events, artifacts, run_events
        ).values()
        for card in group
    ]
    card = next((candidate for candidate in cards if candidate.id == task_id), None)
    if card is None:
        return None
    reports = _reports_for_card(card, list(artifacts))
    return TaskDetail(
        card=card,
        reports=reports,
        logs=[],
        activity_groups=canonical_activity_groups_for_run(
            tool_events, task_id=task_id, run_events=run_events
        ),
    )


def canonical_delivery_overview_for_run(
    *,
    run_id: str,
    run_events: Sequence[Any],
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    status: str,
    published_url: str = "",
) -> DeliveryOverview:
    """Build Overview metrics from structured trace and registry records only."""

    board_groups = canonical_board_cards_for_run(Path(), tool_events, artifacts, run_events)
    cards = [card for cards in board_groups.values() for card in cards]
    features = [
        FeatureProgress(
            feature_id=card.id,
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
        for card in sorted(cards, key=lambda card: (card.sprint != "Planning", card.order, card.id))
    ]
    latest_tool = _latest_payload(tool_events)
    qa_status = _canonical_quality_status(tool_events, run_events)
    deployment_status = _latest_tool_status(
        tool_events,
        {"run_deployment"},
        success="deployed",
        default="pending",
    )
    handoff_status = _latest_tool_status(
        tool_events,
        {"run_handoff"},
        success="ready",
        default="pending",
    )
    stage = _canonical_stage_from_trace(run_events, latest_tool, status)
    deployment_targets = (
        [DeploymentTarget(label="Open App", url=published_url, service="generated-app")]
        if published_url
        else []
    )
    return DeliveryOverview(
        run_id=run_id,
        stage=stage,
        status=status or "running",
        active_feature_id=str(latest_tool.get("work_item_id") or "") or None,
        features=features,
        qa_status=qa_status,
        deployment_status=deployment_status,
        handoff_status=handoff_status,
        topology_summary=_canonical_topology_summary(tool_events, artifacts),
        deployment_targets=deployment_targets,
        blockers=_canonical_blockers(tool_events),
        team_lead_steps=[],
        current_work=None,
    )


def canonical_run_timing_for_trace(
    run_events: Sequence[Any],
    tool_events: Sequence[Any],
    *,
    completed: bool,
) -> dict[str, str]:
    payloads = [
        *(_event_payload(event) for event in run_events),
        *(_event_payload(event) for event in tool_events),
    ]
    timestamps = [
        str(payload.get("created_at") or "")
        for payload in payloads
        if str(payload.get("created_at") or "")
    ]
    if not timestamps:
        return {}
    start = min(timestamps)
    terminal = [
        str(payload.get("created_at") or "")
        for payload in payloads
        if _trace_status_terminal(str(payload.get("status") or ""))
        and str(payload.get("created_at") or "")
    ]
    end = max(terminal or timestamps) if completed else ""
    return _timing_payload(start, end)


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


def _latest_payload(events: Sequence[Any]) -> dict[str, Any]:
    payloads = [_event_payload(event) for event in events]
    payloads = [payload for payload in payloads if payload]
    if not payloads:
        return {}
    return sorted(payloads, key=lambda payload: str(payload.get("created_at") or ""))[-1]


def _latest_tool_status(
    tool_events: Sequence[Any],
    tool_names: set[str],
    *,
    success: str,
    default: str,
) -> str:
    events = [
        _event_payload(event)
        for event in tool_events
        if str(_event_payload(event).get("tool_name") or "") in tool_names
    ]
    if not events:
        return default
    latest = sorted(events, key=lambda event: str(event.get("created_at") or ""))[-1]
    status = str(latest.get("status") or "").lower()
    failure = str(latest.get("failure_mode") or "").lower()
    if failure or any(token in status for token in ("failed", "blocked", "human_approval")):
        return "failed" if "human_approval" not in status else "human_approval_required"
    if any(token in status for token in ("passed", "ready", "deployed", "completed", "done")):
        return success
    return status or default


def _canonical_quality_status(
    tool_events: Sequence[Any],
    run_events: Sequence[Any],
) -> str:
    quality_events = [
        _event_payload(event)
        for event in tool_events
        if str(_event_payload(event).get("tool_name") or "")
        in {"run_qa", "run_post_deploy_qa"}
    ]
    if not quality_events:
        return "pending"
    latest_quality = sorted(quality_events, key=lambda event: str(event.get("created_at") or ""))[
        -1
    ]
    latest_quality_time = str(latest_quality.get("created_at") or "")
    latest_quality_item = _canonical_work_item_id(
        str(latest_quality.get("work_item_id") or ""), latest_quality
    )
    status = str(latest_quality.get("status") or "").lower()
    failure = str(latest_quality.get("failure_mode") or "").lower()
    if failure or any(token in status for token in ("failed", "blocked", "human_approval")):
        later_tool_events = [
            _event_payload(event)
            for event in tool_events
            if str(_event_payload(event).get("created_at") or "") > latest_quality_time
        ]
        later_same_item = [
            event
            for event in later_tool_events
            if _canonical_work_item_id(str(event.get("work_item_id") or ""), event)
            == latest_quality_item
        ]
        if any(
            str(event.get("tool_name") or "") in {"run_fullstack", "codex_exec"}
            and any(
                token in str(event.get("status") or "").lower()
                for token in ("completed", "implemented", "succeeded")
            )
            for event in later_same_item
        ):
            later_qa_started = any(
                str(_event_payload(event).get("created_at") or "") > latest_quality_time
                and _canonical_task_id(
                    str((_event_payload(event).get("data") or {}).get("feature_id") or "")
                )
                == latest_quality_item
                and str(_event_payload(event).get("event_type") or "")
                in {"qa_started", "qa_codex_started"}
                for event in run_events
                if isinstance(_event_payload(event).get("data"), dict)
            )
            return "review" if later_qa_started else "pending"
        return "failed" if "human_approval" not in status else "human_approval_required"
    if any(token in status for token in ("passed", "ready", "completed", "done")):
        return "passed"
    if any(token in status for token in ("running", "started", "review", "limited")):
        return "review"
    return status or "pending"


def _canonical_stage_from_trace(
    run_events: Sequence[Any],
    latest_tool: dict[str, Any],
    status: str,
) -> str:
    if status in {"complete", "completed"}:
        return "release"
    if status in {"blocked", "failed", "stopped"}:
        return status
    tool_name = str(latest_tool.get("tool_name") or "")
    if tool_name == "run_handoff":
        return "release"
    if tool_name in {"run_deployment", "run_post_deploy_qa"}:
        return "publishing"
    if tool_name == "run_qa":
        return "quality_review"
    if tool_name == "run_fullstack":
        return "build"
    if tool_name in {"run_business_analyst", "run_architect", "run_project_manager"}:
        return "planning"
    latest_run = _latest_payload(run_events)
    return str(latest_run.get("status") or "running") or "running"


def _canonical_topology_summary(
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
) -> str:
    for event in reversed([_event_payload(event) for event in tool_events]):
        if str(event.get("tool_name") or "") not in {"run_deployment", "run_handoff"}:
            continue
        comment = _dashboard_comment_for_event(event)
        if comment:
            return comment
    if any(artifact.artifact_type == "deployment_summary" for artifact in artifacts):
        return "The generated application has deployment evidence attached to this run."
    if any(artifact.artifact_type == "release_report" for artifact in artifacts):
        return "The release report is ready for review."
    return ""


def _canonical_blockers(tool_events: Sequence[Any]) -> list[str]:
    latest_by_item: dict[str, dict[str, Any]] = {}
    fallback_key = 0
    for event in (_event_payload(event) for event in tool_events):
        item_id = _canonical_work_item_id(str(event.get("work_item_id") or ""), event)
        if not item_id:
            fallback_key += 1
            item_id = f"event-{fallback_key}"
        current = latest_by_item.get(item_id)
        if current is None or str(event.get("created_at") or "") >= str(
            current.get("created_at") or ""
        ):
            latest_by_item[item_id] = event

    blockers: list[str] = []
    for event in latest_by_item.values():
        status = str(event.get("status") or "").lower()
        failure = str(event.get("failure_mode") or "").strip()
        if not failure and not any(token in status for token in ("failed", "blocked")):
            continue
        summary = _dashboard_comment_for_event(event)
        if summary and summary not in blockers:
            blockers.append(summary)
    return blockers[-5:]


def _legacy_artifacts_for_run(run_dir: Path) -> tuple[list[ArtifactView], list[ArtifactView]]:
    business: list[ArtifactView] = []
    technical: list[ArtifactView] = []
    task_titles = _task_title_map(run_dir)
    task_sprints = _task_sprint_map(run_dir)
    for _, _, artifacts in artifact_groups_for_run(run_dir):
        for path, label, agent in artifacts:
            if not _safe_artifact_path(run_dir, path).exists():
                continue
            if not is_user_facing_artifact(path):
                continue
            task_id = _task_id_for_artifact(path)
            view = ArtifactView(
                path=path,
                label=user_friendly_artifact_label(label, path),
                agent=agent,
                business_agent=business_agent_label(agent),
                kind=artifact_kind(path),
                technical=is_technical_artifact(path),
                phase=task_sprints.get(task_id, _artifact_phase(path, agent)),
                task_id=task_id,
                task_title=task_titles.get(task_id, ""),
            )
            if view.technical:
                technical.append(view)
            else:
                business.append(view)
    return sorted(business, key=_artifact_sort_key), sorted(technical, key=_artifact_sort_key)


def _registry_artifacts_for_run(run_dir: Path) -> tuple[list[ArtifactView], list[ArtifactView]]:
    business: list[ArtifactView] = []
    technical: list[ArtifactView] = []
    task_titles = _task_title_map(run_dir)
    task_sprints = _task_sprint_map(run_dir)
    for record in list_artifacts(run_dir):
        if record.visibility == "internal":
            continue
        if not _safe_artifact_path(run_dir, record.relative_path).exists():
            continue
        if not is_user_facing_artifact_record(record):
            continue
        task_id = record.work_item_id or _task_id_for_artifact(record.relative_path)
        view = ArtifactView(
            path=record.relative_path,
            label=user_friendly_artifact_label_for_record(record),
            agent=record.owner_agent,
            business_agent=business_agent_label(record.owner_agent),
            kind=artifact_kind(record.relative_path),
            technical=False,
            phase=task_sprints.get(
                task_id,
                _artifact_phase(record.relative_path, record.owner_agent),
            ),
            task_id=task_id,
            task_title=task_titles.get(task_id, ""),
            artifact_id=record.artifact_id,
            visibility=record.visibility,
            artifact_type=record.artifact_type,
        )
        if view.technical:
            technical.append(view)
        else:
            business.append(view)
    return sorted(business, key=_artifact_sort_key), sorted(technical, key=_artifact_sort_key)


def _artifact_view_from_record(
    record: Any,
    task_titles: dict[str, str],
    task_sprints: dict[str, str],
) -> ArtifactView:
    task_id = str(record.work_item_id or _task_id_for_artifact(record.relative_path))
    return ArtifactView(
        path=record.relative_path,
        label=user_friendly_artifact_label_for_record(record),
        agent=record.owner_agent,
        business_agent=business_agent_label(record.owner_agent),
        kind=artifact_kind(record.relative_path),
        technical=not is_user_facing_artifact_record(record),
        phase=task_sprints.get(task_id, _artifact_phase(record.relative_path, record.owner_agent)),
        task_id=task_id,
        task_title=task_titles.get(task_id, ""),
        artifact_id=record.artifact_id,
        visibility=record.visibility,
        artifact_type=record.artifact_type,
    )


def _artifact_record_file_exists(run_dir: Path, record: Any) -> bool:
    try:
        return _safe_artifact_path(run_dir, record.relative_path).exists()
    except ValueError:
        return False


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
    if normalized.startswith("delivery/") or normalized.startswith("generated-project/"):
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
    return user_friendly_artifact_label(str(record.label or ""), str(record.relative_path))


def _backfill_visible_legacy_artifacts(run_dir: Path) -> None:
    for view in _legacy_artifacts_for_run(run_dir)[0]:
        register_artifact(
            run_dir,
            relative_path=view.path,
            owner_agent=view.agent,
            artifact_type=_semantic_artifact_type(view.path),
            visibility=_registry_visibility_for_path(view.path),
            label=view.label,
            source_tool="legacy_artifact_groups_for_run",
            work_item_id=view.task_id or None,
            metadata={
                "legacy_kind": view.kind,
                "implicit_resolution_warnings": [
                    f"artifact_type inferred from path {view.path}",
                    "visibility inferred from legacy user-facing filename rules",
                ],
            },
        )


def _semantic_artifact_type(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith("business-analysis.md"):
        return "requirements_brief"
    if normalized.endswith("architecture.md") or normalized.endswith("architecture.mmd"):
        return "architecture_report"
    if any(token in normalized for token in ("project-management", "release-plan", "roadmap")):
        return "delivery_plan"
    if normalized.startswith("07-execution-summary"):
        return "execution_summary"
    if normalized.startswith("08-qa-report"):
        return "qa_report"
    if normalized.startswith("13-deployment-summary"):
        return "deployment_summary"
    if normalized.endswith("release-report.html"):
        return "release_report"
    return "artifact"


def _registry_visibility_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith("release-report.html"):
        return "release"
    if normalized.startswith("08-qa-report"):
        return "qa_evidence"
    return "business"


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
        grouped.setdefault(artifact.phase, []).append(artifact)
    ordered = [phase for phase in _PHASE_ORDER if phase in grouped]
    ordered.extend(sorted(phase for phase in grouped if phase not in _PHASE_ORDER))
    return [
        {"phase": phase, "artifacts": grouped[phase], "count": len(grouped[phase])}
        for phase in ordered
    ]


def task_report_groups_for_run(
    run_dir: Path,
    artifacts: list[ArtifactView],
) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for sprint, cards in board_groups_for_run(run_dir).items():
        task_rows = []
        for card in cards:
            reports = _reports_for_card(card, artifacts)
            task_rows.append(
                {
                    "card": card,
                    "reports": reports,
                    "count": len(reports),
                }
            )
        groups.append({"sprint": sprint, "tasks": task_rows, "count": len(task_rows)})
    return groups


def sprint_board_groups_for_run(run_dir: Path) -> list[dict[str, object]]:
    cards_by_sprint = board_groups_for_run(run_dir)
    groups: list[dict[str, object]] = []
    for sprint, cards in cards_by_sprint.items():
        columns: dict[str, list[BoardCard]] = {key: [] for key, _ in BOARD_COLUMNS}
        for card in cards:
            columns.setdefault(card.column, []).append(card)
        groups.append(
            {
                "sprint": sprint,
                "count": len(cards),
                "columns": columns,
            }
        )
    return groups


def work_plan_groups_for_run(run_dir: Path) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    planning_cards = _planning_cards_for_run(run_dir)
    if planning_cards:
        groups.append(
            {
                "name": "Planning",
                "count": len(planning_cards),
                "cards": planning_cards,
            }
        )
    for sprint, cards in sprint_groups_for_run(run_dir).items():
        if sprint == "Planning" and planning_cards:
            continue
        groups.append(
            {
                "name": sprint,
                "count": len(cards),
                "cards": cards,
            }
        )
    return groups


def board_groups_for_run(run_dir: Path) -> dict[str, list[BoardCard]]:
    groups = {"Planning": _planning_cards_for_run(run_dir)}
    for sprint, cards in sprint_groups_for_run(run_dir).items():
        if sprint == "Planning" and groups["Planning"]:
            continue
        groups[sprint] = cards
    return groups


def sprint_groups_for_run(run_dir: Path) -> dict[str, list[BoardCard]]:
    unsorted: dict[str, list[BoardCard]] = {}
    for cards in board_cards_for_run(run_dir).values():
        for card in cards:
            unsorted.setdefault(sprint_label(card.sprint or "Planning"), []).append(card)
    groups: dict[str, list[BoardCard]] = {}
    for sprint in sorted(unsorted, key=_sprint_sort_key):
        groups[sprint] = sorted(unsorted[sprint], key=lambda card: (card.order, card.id))
    return groups


def sprint_label(value: str) -> str:
    token = (value or "Planning").strip()
    match = re.fullmatch(r"sprint-(\d+)", token, flags=re.IGNORECASE)
    if match:
        return f"Sprint {int(match.group(1))}"
    return status_label(token) if "_" in token else token.replace("-", " ").title()


def _planning_cards_for_run(run_dir: Path) -> list[BoardCard]:
    timings = _card_timings_for_run(run_dir)
    steps = [
        (
            "PLAN-01",
            "Requirements brief",
            "Business Analyst",
            run_dir / "upstream-planning" / "business-analysis.md",
            1,
        ),
        (
            "PLAN-02",
            "Solution overview",
            "Solution Architect",
            run_dir / "upstream-planning" / "architecture.md",
            2,
        ),
        (
            "PLAN-03",
            "Delivery plan",
            "Delivery Planner",
            run_dir / "upstream-planning" / "project-management" / "release-plan.md",
            3,
        ),
        (
            "PLAN-04",
            "Sprint delivery coordination",
            "Delivery Lead",
            run_dir / "team-lead" / "work-board.json",
            4,
        ),
    ]
    cards: list[BoardCard] = []
    for item_id, title, owner, path, order in steps:
        artifact_exists = path.exists()
        timing = timings.get(item_id, {})
        started = bool(timing.get("started_at"))
        completed = bool(timing.get("completed_at"))
        done = artifact_exists and (completed or not started)
        in_progress = started and not completed
        needs_attention = completed and not artifact_exists
        status = (
            "Done"
            if done
            else "In Progress"
            if in_progress
            else "Needs attention"
            if needs_attention
            else "Pending"
        )
        column = (
            "done"
            if done
            else "in_progress"
            if in_progress
            else "blocked"
            if needs_attention
            else "todo"
        )
        cards.append(
            BoardCard(
                id=item_id,
                title=title,
                owner=owner,
                sprint="Planning",
                status=status,
                column=column,
                artifact_count=1 if artifact_exists else 0,
                active=in_progress,
                order=order,
                started_at=timing.get("started_at", ""),
                completed_at=timing.get("completed_at", ""),
                elapsed_label=timing.get("elapsed_label", ""),
            )
        )
    return cards


def _sprint_sort_key(value: str) -> tuple[int, int, str]:
    if value == "Planning":
        return (0, 0, value)
    match = re.fullmatch(r"Sprint (\d+)", value, flags=re.IGNORECASE)
    if match:
        return (1, int(match.group(1)), value)
    return (2, 0, value)


def live_log_entries_for_run(run_dir: Path) -> list[str]:
    events = read_events(run_dir)
    entries = friendly_log_entries(
        events,
        _read_codex_events(run_dir),
        qa_log=run_dir / "qa" / "command-progress.log",
        deployment_log=run_dir / "deployment" / "command-progress.log",
    )
    cleaned = [_business_log_text(entry) for entry in entries]
    user_facing = [entry for entry in cleaned if _is_user_facing_log_entry(entry)]
    logs = sorted(
        [*_coordinator_activity_entries(events), *user_facing],
        key=_log_sort_key,
    )
    return logs if execution_completed(run_dir) else logs[-180:]


def rendered_log_entries_for_run(run_dir: Path) -> list[str]:
    return [render_markdown(entry) for entry in live_log_entries_for_run(run_dir)]


def activity_groups_for_run(run_dir: Path) -> list[dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    for entry in live_log_entries_for_run(run_dir):
        owner = _activity_owner(entry, "Delivery")
        grouped.setdefault(owner, []).append(render_markdown(entry))
    return _ordered_activity_groups(grouped)


def task_detail_for_run(
    run_dir: Path,
    task_id: str,
    artifacts: list[ArtifactView] | None = None,
) -> TaskDetail | None:
    task_id = _canonical_task_id(task_id.strip())
    cards = [card for group in board_groups_for_run(run_dir).values() for card in group]
    card = next((candidate for candidate in cards if candidate.id == task_id), None)
    if card is None:
        return None
    visible_artifacts = artifacts if artifacts is not None else artifacts_for_run(run_dir)[0]
    reports = _reports_for_card(card, visible_artifacts)
    raw_logs = [
        entry for entry in live_log_entries_for_run(run_dir) if _log_matches_card(entry, card)
    ]
    logs = [render_markdown(entry) for entry in raw_logs]
    return TaskDetail(
        card=card,
        reports=reports,
        logs=logs[-40:],
        activity_groups=_activity_groups_for_card(card, raw_logs[-40:]),
    )


def run_timing_for_run(run_dir: Path) -> dict[str, str]:
    timestamps = [
        str(event.get("timestamp") or "")
        for event in read_events(run_dir)
        if str(event.get("timestamp") or "")
    ]
    if not timestamps:
        return {}
    start = min(timestamps)
    end = max(timestamps) if execution_completed(run_dir) else ""
    return _timing_payload(start, end)


def _card_timings_for_run(run_dir: Path) -> dict[str, dict[str, str]]:
    timings: dict[str, dict[str, str]] = {}
    planning_events = {
        "PLAN-01": ("business_analysis_started", "business_analysis_completed"),
        "PLAN-02": ("architecture_started", "architecture_completed"),
        "PLAN-03": ("project_management_started", "project_management_completed"),
        "PLAN-04": ("team_lead_sprint_started", "team_lead_sprint_completed"),
    }
    events = read_events(run_dir)
    for item_id, (started_event, completed_event) in planning_events.items():
        start = _first_event_timestamp(events, started_event)
        end = _last_event_timestamp(events, completed_event)
        if start:
            timings[item_id] = _timing_payload(start, end)

    feature_events: dict[str, list[dict[str, object]]] = {}
    for event in events:
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        feature_id = str(
            data.get("feature_id") or data.get("active_feature_id") or data.get("target") or ""
        ).strip()
        if _is_work_item_id(feature_id):
            feature_events.setdefault(feature_id, []).append(event)

    for feature_id, feature_history in feature_events.items():
        timestamps = [
            str(event.get("timestamp") or "")
            for event in feature_history
            if str(event.get("timestamp") or "")
        ]
        if not timestamps:
            continue
        start = min(timestamps)
        completed = [
            str(event.get("timestamp") or "")
            for event in feature_history
            if _event_looks_complete(event)
        ]
        timings[feature_id] = _timing_payload(start, max(completed) if completed else "")
    return timings


def _apply_role_timing_fallbacks(grouped: dict[str, list[BoardCard]], run_dir: Path) -> None:
    final_report_timing = _final_project_report_timing_for_run(run_dir)
    if not final_report_timing:
        return
    for column, cards in grouped.items():
        updated_cards = []
        for card in cards:
            if card.elapsed_label:
                updated_cards.append(card)
                continue
            if card.owner == "Release Reporter" or _looks_like_release_report_card(card):
                updated_cards.append(
                    replace(
                        card,
                        started_at=final_report_timing.get("started_at", ""),
                        completed_at=final_report_timing.get("completed_at", ""),
                        elapsed_label=final_report_timing.get("elapsed_label", ""),
                    )
                )
            else:
                updated_cards.append(card)
        grouped[column] = updated_cards


def _canonical_cards_from_trace(
    run_dir: Path,
    tool_events: Sequence[Any],
    artifacts: Sequence[ArtifactView],
    run_events: Sequence[Any] = (),
) -> list[BoardCard]:
    feature_catalog = _feature_catalog_for_run(run_dir)
    event_map: dict[str, list[dict[str, Any]]] = {}
    all_events = [_run_event_as_work_event(event) for event in run_events]
    all_events.extend(_event_payload(event) for event in tool_events)
    all_events = [event for event in all_events if event]
    for payload in all_events:
        item_id = _canonical_work_item_id(str(payload.get("work_item_id") or ""), payload)
        if item_id and _is_canonical_board_item_id(item_id):
            event_map.setdefault(item_id, []).append(payload)

    cards: dict[str, BoardCard] = {}
    for tool_name, (item_id, title, owner, order) in PLANNING_TOOL_ITEMS.items():
        events = [
            event
            for event in event_map.get(item_id, [])
            if str(event.get("tool_name") or "") == tool_name
        ] or [event for event in all_events if str(event.get("tool_name") or "") == tool_name]
        if events or any(artifact.task_id == item_id for artifact in artifacts):
            cards[item_id] = _card_from_events(
                item_id=item_id,
                title=title,
                owner=owner,
                sprint="Planning",
                order=order,
                events=events,
                artifacts=artifacts,
                feature_catalog=feature_catalog,
            )

    delivery_ids = sorted(
        {
            item_id
            for item_id in event_map
            if item_id not in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"} and item_id
        },
        key=_work_item_sort_key,
    )
    for index, item_id in enumerate(delivery_ids, start=10):
        events = event_map[item_id]
        cards[item_id] = _card_from_events(
            item_id=item_id,
            title=_title_for_work_item(item_id, events, artifacts, feature_catalog),
            owner=_owner_for_work_item(item_id, events, feature_catalog),
            sprint=_sprint_for_work_item(item_id, events, feature_catalog),
            order=index,
            events=events,
            artifacts=artifacts,
            feature_catalog=feature_catalog,
        )

    for item_id, feature in feature_catalog.items():
        if item_id in cards:
            continue
        cards[item_id] = BoardCard(
            id=item_id,
            title=str(feature.get("title") or feature.get("name") or item_id),
            owner=business_agent_label(
                str(feature.get("owner_agent") or feature.get("suggested_owner_agent") or "Builder")
            ),
            sprint=str(feature.get("sprint_id") or "Sprint 1"),
            status=work_status_label(str(feature.get("status") or "pending")),
            column="todo",
            artifact_count=sum(1 for artifact in artifacts if artifact.task_id == item_id),
            active=False,
            order=_work_item_catalog_order(item_id, feature),
        )

    for artifact in artifacts:
        if not artifact.task_id or artifact.task_id in cards:
            continue
        if artifact.task_id in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"}:
            title = _title_for_artifact_task(artifact.task_id)
            owner = artifact.business_agent
            sprint = "Planning"
            order = {"PLAN-01": 1, "PLAN-02": 2, "PLAN-03": 3, "PLAN-04": 4}.get(
                artifact.task_id,
                99,
            )
        elif artifact.task_id == "DEPLOY":
            title = "Deploy generated app"
            owner = "Publisher"
            sprint = artifact.phase or "Sprint 1"
            order = 90
        else:
            title = artifact.task_title or artifact.label
            owner = artifact.business_agent
            sprint = artifact.phase or "Sprint 1"
            order = 99
        cards[artifact.task_id] = BoardCard(
            id=artifact.task_id,
            title=title,
            owner=owner,
            sprint=sprint,
            status="Done",
            column="done",
            artifact_count=sum(1 for item in artifacts if item.task_id == artifact.task_id),
            active=False,
            order=order,
        )
    return list(cards.values())


def _feature_catalog_for_run(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load planned feature metadata so canonical board cards do not depend on filenames."""

    queue_path = (
        run_dir / "upstream-planning" / "project-management" / "candidate-feature-queue.json"
    )
    try:
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, list):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        item_id = _canonical_task_id(str(item.get("id") or ""))
        if item_id and _is_canonical_board_item_id(item_id):
            catalog[item_id] = item
    return catalog


def _card_from_events(
    *,
    item_id: str,
    title: str,
    owner: str,
    sprint: str,
    order: int,
    events: Sequence[dict[str, Any]],
    artifacts: Sequence[ArtifactView],
    feature_catalog: dict[str, dict[str, Any]],
) -> BoardCard:
    status = _board_status_from_tool_events(item_id, events)
    column = _board_column("", status)
    timing = _timing_from_tool_events(events)
    feature = feature_catalog.get(item_id, {})
    return BoardCard(
        id=item_id,
        title=str(feature.get("title") or title),
        owner=owner,
        sprint=str(feature.get("sprint_id") or sprint),
        status=work_status_label(status),
        column=column,
        artifact_count=sum(1 for artifact in artifacts if artifact.task_id == item_id),
        active=column == "in_progress",
        order=order,
        started_at=timing.get("started_at", ""),
        completed_at=timing.get("completed_at", ""),
        elapsed_label=timing.get("elapsed_label", ""),
    )


def _event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "to_dict"):
        return event.to_dict()
    if isinstance(event, dict):
        return event
    return {}


def _run_event_as_work_event(event: Any) -> dict[str, Any]:
    payload = _event_payload(event)
    event_type = str(payload.get("event_type") or payload.get("event") or "")
    if event_type.startswith("codex_command"):
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    work_item_id = _work_item_for_run_event(payload, data, event_type)
    if not work_item_id:
        return {}
    status = str(payload.get("status") or data.get("status") or "")
    if not status:
        status = _status_for_run_event(event_type)
    return {
        "agent_id": str(payload.get("agent_id") or ""),
        "created_at": str(payload.get("created_at") or payload.get("timestamp") or ""),
        "event_type": event_type,
        "input_summary": data,
        "message": _message_for_run_event(payload, data, event_type),
        "output_summary": {
            "dashboard_update": {
                "comment": _message_for_run_event(payload, data, event_type),
                "status": _dashboard_status_for_run_event(event_type, status),
                "summary": _message_for_run_event(payload, data, event_type),
            }
        },
        "status": status,
        "tool_name": _tool_name_for_run_event(work_item_id),
        "work_item_id": work_item_id,
    }


def _is_developer_only_run_event(payload: dict[str, Any]) -> bool:
    event_type = str(payload.get("event_type") or payload.get("event") or "")
    return event_type.startswith("codex_command")


def _activity_dedupe_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip().lower()


def _sorted_activity_payloads(payloads: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        payload
        for _, payload in sorted(
            enumerate(payloads),
            key=lambda item: (_activity_payload_sort_timestamp(item[1]), item[0]),
        )
    ]


def _activity_payload_sort_timestamp(payload: dict[str, Any]) -> str:
    timestamp = str(payload.get("created_at") or payload.get("timestamp") or "")
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return "9999-12-31T23:59:59Z"
    return parsed.isoformat(timespec="microseconds")


def _work_item_for_run_event(payload: dict[str, Any], data: dict[str, Any], event_type: str) -> str:
    explicit = _canonical_task_id(
        str(payload.get("work_item_id") or data.get("work_item_id") or "")
    )
    if explicit and _is_canonical_board_item_id(explicit):
        return explicit
    node = str(data.get("node") or data.get("stage") or "")
    token = f"{event_type} {node}".lower()
    if "business_analyst" in token or "business_analysis" in token:
        return "PLAN-01"
    if "architecture" in token or "architect" in token:
        return "PLAN-02"
    if "project_management" in token or "project_manager" in token:
        return "PLAN-03"
    if "deployment" in token or "deploy" in token:
        return "DEPLOY"
    feature_id = _canonical_task_id(
        str(
            data.get("active_feature_id")
            or data.get("feature_id")
            or payload.get("work_item_id")
            or ""
        )
    )
    if feature_id and _is_canonical_board_item_id(feature_id):
        return feature_id
    return ""


def _status_for_run_event(event_type: str) -> str:
    token = event_type.lower()
    if "work_item_planned" in token:
        return "pending"
    if any(value in token for value in ("completed", "passed", "ready", "deployed")):
        return "done"
    if any(value in token for value in ("failed", "blocked")):
        return "blocked"
    if any(value in token for value in ("started", "selected", "running")):
        return "in_progress"
    return "in_progress"


def _dashboard_status_for_run_event(event_type: str, status: str) -> str:
    token = f"{event_type} {status}".lower()
    if "work_item_planned" in token or any(
        value in token for value in ("pending", "todo", "planned")
    ):
        return "pending"
    if any(value in token for value in ("completed", "passed", "ready", "deployed", "done")):
        return "done"
    if any(value in token for value in ("failed", "blocked")):
        return "blocked"
    return "in_progress"


def _tool_name_for_run_event(work_item_id: str) -> str:
    return {
        "PLAN-01": "run_business_analyst",
        "PLAN-02": "run_architect",
        "PLAN-03": "run_project_manager",
        "PLAN-04": "run_team_lead",
        "DEPLOY": "run_deployment",
    }.get(work_item_id, "runtime_progress")


def _message_for_run_event(payload: dict[str, Any], data: dict[str, Any], event_type: str) -> str:
    message = str(payload.get("message") or data.get("message") or "").strip()
    if message and message != event_type:
        return message
    label = {
        "business_analysis_started": "Business Analyst started working.",
        "business_analysis_codex_started": "Business Analyst is preparing requirements.",
        "business_analysis_completed": "Requirements brief is ready.",
        "business_analysis_codex_completed": "Business Analyst completed the requirements pass.",
        "architecture_started": "Solution Architect started working.",
        "architecture_codex_started": "Solution Architect is preparing the solution overview.",
        "architecture_completed": "Solution overview is ready.",
        "architecture_codex_completed": "Solution Architect completed the architecture pass.",
        "project_management_started": "Delivery Planner started working.",
        "project_management_codex_started": "Delivery Planner is preparing the work plan.",
        "project_management_completed": "Delivery plan is ready.",
        "project_management_codex_completed": "Delivery Planner completed the planning pass.",
        "deployment_started": "Publisher started deployment.",
    }.get(event_type)
    if label:
        return label
    node = str(data.get("node") or "").replace("_", " ").strip()
    if event_type == "head_worker_started" and node:
        return f"Coordinator routed work to {node}."
    if event_type == "head_worker_completed" and node:
        return f"Coordinator received completed {node} work."
    return event_type.replace("_", " ").title()


def _dashboard_update(event: dict[str, Any]) -> dict[str, Any]:
    output = event.get("output_summary")
    if not isinstance(output, dict):
        return {}
    update = output.get("dashboard_update")
    return update if isinstance(update, dict) else {}


def _dashboard_comment_for_event(event: dict[str, Any]) -> str:
    update = _dashboard_update(event)
    values = [
        update.get("comment"),
        update.get("summary"),
        event.get("business_summary"),
    ]
    output = event.get("output_summary")
    if isinstance(output, dict):
        values.extend(
            [output.get("business_summary"), output.get("summary"), output.get("message")]
        )
    for value in values:
        if str(value or "").strip():
            return _business_log_text(str(value).strip())
    tool_name = str(event.get("tool_name") or "").replace("_", " ").title()
    status = status_label(str(event.get("status") or ""))
    return f"{tool_name} {status}".strip()


def _canonical_work_item_id(work_item_id: str, event: dict[str, Any]) -> str:
    tool_name = str(event.get("tool_name") or "")
    if tool_name in PLANNING_TOOL_ITEMS:
        return PLANNING_TOOL_ITEMS[tool_name][0]
    if tool_name == "run_deployment":
        return "DEPLOY"
    if tool_name == "run_post_deploy_qa":
        return "DEPLOY"
    candidate = _canonical_task_id(work_item_id.strip())
    if candidate and _is_canonical_board_item_id(candidate):
        return candidate
    input_summary = event.get("input_summary")
    if isinstance(input_summary, dict):
        for key in ("work_item_id", "feature_id", "target"):
            value = str(input_summary.get(key) or "").strip()
            candidate = _canonical_task_id(value)
            if candidate and _is_canonical_board_item_id(candidate):
                return candidate
    output_summary = event.get("output_summary")
    if isinstance(output_summary, dict):
        for key in ("work_item_id", "feature_id", "target"):
            value = str(output_summary.get(key) or "").strip()
            candidate = _canonical_task_id(value)
            if candidate and _is_canonical_board_item_id(candidate):
                return candidate
    return ""


def _is_canonical_board_item_id(value: str) -> bool:
    return value in {
        "PLAN-01",
        "PLAN-02",
        "PLAN-03",
        "PLAN-04",
        "DEPLOY",
    } or _is_work_item_id(value)


def _board_status_from_tool_events(item_id: str, events: Sequence[dict[str, Any]]) -> str:
    latest_state = ""
    for event in _sorted_activity_payloads(list(events)):
        state = _board_state_for_event(item_id, event)
        if state:
            latest_state = state
    if latest_state:
        return latest_state
    if events:
        return "in_progress"
    return "done"


def _timing_from_tool_events(events: Sequence[dict[str, Any]]) -> dict[str, str]:
    ordered = _sorted_activity_payloads(list(events))
    timestamps = [
        str(event.get("created_at") or "") for event in ordered if event.get("created_at")
    ]
    if not timestamps:
        return {}
    start = min(timestamps)
    latest_event = ordered[-1] if ordered else {}
    latest_state = _board_state_for_event("", latest_event)
    end = str(latest_event.get("created_at") or "") if latest_state in {"done", "blocked"} else ""
    if not end:
        return _timing_payload(start, "")
    if start == end:
        durations = [
            int(duration)
            for event in ordered
            if (duration := event.get("duration_ms")) is not None
            and isinstance(duration, int | float)
            and duration > 0
        ]
        parsed_end = _parse_timestamp(end)
        if durations and parsed_end:
            start = (
                (parsed_end - timedelta(milliseconds=max(durations)))
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
    return _timing_payload(start, end)


def _board_state_for_event(item_id: str, event: dict[str, Any]) -> str:
    update_status = str(_dashboard_update(event).get("status") or "").lower()
    status = str(event.get("status") or "").lower()
    failure = str(event.get("failure_mode") or "").lower()
    token = f"{update_status} {status} {failure}".strip()
    if not token:
        return ""
    if any(value in token for value in ("failed", "blocked", "human_approval")):
        return "blocked"
    if item_id == "DEPLOY" and any(value in token for value in ("deployed", "post_deploy")):
        return "done"
    if any(value in token for value in ("pending", "todo", "planned")):
        return "pending"
    if "qa_passed" in token:
        return "done"
    if any(value in token for value in ("review", "quality_review")):
        return "review"
    if "implemented" in token or "codex_completed" in token:
        return "review"
    if any(value in token for value in ("done", "passed", "deployed", "ready", "closed")):
        return "done"
    if any(value in token for value in ("in_progress", "running", "active", "started")):
        return "in_progress"
    return ""


def _trace_status_terminal(status: str) -> bool:
    token = status.lower()
    return any(
        value in token
        for value in ("succeeded", "completed", "passed", "done", "failed", "blocked", "deployed")
    )


def _title_for_work_item(
    item_id: str,
    events: Sequence[dict[str, Any]],
    artifacts: Sequence[ArtifactView],
    feature_catalog: dict[str, dict[str, Any]],
) -> str:
    if item_id in feature_catalog and feature_catalog[item_id].get("title"):
        return str(feature_catalog[item_id]["title"])
    artifact_title = next(
        (
            artifact.task_title
            for artifact in artifacts
            if artifact.task_id == item_id and artifact.task_title
        ),
        "",
    )
    if artifact_title:
        return artifact_title
    for event in reversed(events):
        for source in (event.get("input_summary"), event.get("output_summary")):
            if not isinstance(source, dict):
                continue
            for key in ("title", "feature_title", "summary"):
                value = str(source.get(key) or "").strip()
                if value and len(value) <= 120:
                    return value
    if item_id == "DEPLOY":
        return "Deploy generated app"
    return f"Work item {item_id}"


def _owner_for_work_item(
    item_id: str,
    events: Sequence[dict[str, Any]],
    feature_catalog: dict[str, dict[str, Any]],
) -> str:
    if item_id == "DEPLOY":
        return "Publisher"
    owner = str(feature_catalog.get(item_id, {}).get("suggested_owner_agent") or "")
    if owner:
        return business_agent_label(owner)
    latest = events[-1] if events else {}
    tool_name = str(latest.get("tool_name") or "")
    if tool_name in {"run_qa", "run_post_deploy_qa"}:
        return "Quality Reviewer"
    return business_agent_label(str(latest.get("agent_id") or "Builder"))


def _sprint_for_work_item(
    item_id: str,
    events: Sequence[dict[str, Any]],
    feature_catalog: dict[str, dict[str, Any]],
) -> str:
    if item_id in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"}:
        return "Planning"
    if feature_catalog.get(item_id, {}).get("sprint_id"):
        return str(feature_catalog[item_id]["sprint_id"])
    for event in reversed(events):
        for source in (event.get("input_summary"), event.get("output_summary")):
            if isinstance(source, dict):
                value = str(source.get("sprint_id") or source.get("sprint") or "").strip()
                if value:
                    return value
    return "sprint-01"


def _work_item_sort_key(item_id: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"F(\d+)", item_id, flags=re.IGNORECASE)
    if match:
        return (0, int(match.group(1)), item_id)
    match = re.fullmatch(r"US-(\d+)", item_id, flags=re.IGNORECASE)
    if match:
        return (0, int(match.group(1)), item_id)
    if item_id.startswith("QA"):
        return (1, 0, item_id)
    if item_id == "DEPLOY":
        return (2, 0, item_id)
    return (3, 0, item_id)


def _work_item_catalog_order(item_id: str, feature: dict[str, Any]) -> int:
    try:
        return int(feature.get("delivery_order") or 0)
    except (TypeError, ValueError):
        return 0 if not item_id else _work_item_sort_key(item_id)[1]


def _title_for_artifact_task(task_id: str) -> str:
    return {
        "PLAN-01": "Requirements brief",
        "PLAN-02": "Solution overview",
        "PLAN-03": "Delivery plan",
        "PLAN-04": "Sprint delivery coordination",
        "DEPLOY": "Deploy generated app",
    }.get(task_id, "")


def _sprint_for_artifact_task(task_id: str, record: Any) -> str:
    if task_id in {"PLAN-01", "PLAN-02", "PLAN-03", "PLAN-04"}:
        return "Planning"
    if task_id == "DEPLOY":
        return "Sprint 1"
    metadata = getattr(record, "metadata", {}) or {}
    if isinstance(metadata, dict) and metadata.get("sprint_id"):
        return str(metadata["sprint_id"])
    return _artifact_phase(str(record.relative_path), str(record.owner_agent))


def _looks_like_release_report_card(card: BoardCard) -> bool:
    token = f"{card.id} {card.title}".lower()
    return any(value in token for value in ("demo-deliverables", "release report", "final report"))


def _final_project_report_timing_for_run(run_dir: Path) -> dict[str, str]:
    events = read_events(run_dir)
    completed_indexes = [
        index
        for index, event in enumerate(events)
        if _event_mentions_final_project_report(event)
        and event.get("event") in {"handoff_completed", "handoff_codex_completed"}
    ]
    if not completed_indexes:
        return {}
    completed_index = completed_indexes[-1]
    completed_at = str(events[completed_index].get("timestamp") or "")
    started_at = ""
    for event in reversed(events[: completed_index + 1]):
        if event.get("event") in {"handoff_started", "handoff_codex_started"}:
            started_at = str(event.get("timestamp") or "")
            break
    if not started_at:
        started_at = completed_at
    return _timing_payload(started_at, completed_at)


def _event_mentions_final_project_report(event: dict[str, object]) -> bool:
    data = event.get("data", {})
    if not isinstance(data, dict):
        return False
    text = " ".join(str(value) for value in data.values())
    return "final_project_report" in text or "handoff/project/final/" in text


def _first_event_timestamp(events: list[dict[str, object]], event_name: str) -> str:
    timestamps = [
        str(event.get("timestamp") or "")
        for event in events
        if event.get("event") == event_name and str(event.get("timestamp") or "")
    ]
    return min(timestamps) if timestamps else ""


def _last_event_timestamp(events: list[dict[str, object]], event_name: str) -> str:
    timestamps = [
        str(event.get("timestamp") or "")
        for event in events
        if event.get("event") == event_name and str(event.get("timestamp") or "")
    ]
    return max(timestamps) if timestamps else ""


def _event_looks_complete(event: dict[str, object]) -> bool:
    name = str(event.get("event") or "")
    data = event.get("data", {})
    status = str(data.get("status") or "") if isinstance(data, dict) else ""
    token = f"{name} {status}".lower()
    return any(value in token for value in ("completed", "passed", "ready", "deployed", "done"))


def _timing_payload(start: str, end: str = "") -> dict[str, str]:
    return {
        "started_at": _browser_timestamp(start),
        "completed_at": _browser_timestamp(end),
        "elapsed_label": _elapsed_label(start, end),
    }


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
    if (
        artifact.business_agent in {"Publisher", "Release Reporter"}
        and card.owner != artifact.business_agent
        and card.id != "DEPLOY"
    ):
        return False
    if card.id.upper().startswith("QA") and artifact.business_agent == "Quality Reviewer":
        return artifact.phase == card.sprint or artifact.task_id in _card_aliases(card)
    card_ids = _card_aliases(card)
    return (
        artifact.task_id in card_ids
        or artifact.task_title == card.title
        or (
            card.sprint == "Planning" and artifact.phase == "Requirements" and "PLAN-01" in card_ids
        )
        or (
            card.sprint == "Planning"
            and artifact.phase == "Solution Design"
            and "PLAN-02" in card_ids
        )
        or (
            card.sprint == "Planning"
            and artifact.phase == "Delivery Planning"
            and "PLAN-03" in card_ids
        )
    )


def _log_matches_card(entry: str, card: BoardCard) -> bool:
    normalized = entry.lower()
    owner = _activity_owner(entry, card.owner)
    card_ids = _card_aliases(card)
    if card.sprint.lower() == "planning" and any(alias.startswith("PLAN-") for alias in card_ids):
        return owner == card.owner
    if card.id == "DEPLOY":
        return owner == "Publisher"
    if card.owner in {"Publisher", "Release Reporter"}:
        release_report_match = _looks_like_release_report_card(card) and any(
            value in normalized for value in ("final project", "final report", "project/final")
        )
        return owner == card.owner and (
            any(alias.lower() in normalized for alias in card_ids)
            or card.title.lower() in normalized
            or release_report_match
        )
    if _is_work_item_id(card.id):
        allowed_owners = {card.owner, "Quality Reviewer", "Delivery Lead"}
        if owner not in allowed_owners:
            return False
        return any(alias.lower() in normalized for alias in card_ids) or (
            bool(card.title) and card.title.lower() in normalized
        )
    values = {card.title.lower()}
    values.update(alias.lower() for alias in card_ids)
    if "PLAN-01" in card_ids:
        values.update({"requirements", "business analysis", "business analyst"})
    elif "PLAN-02" in card_ids:
        values.update({"architecture", "solution design", "solution architect"})
    elif "PLAN-03" in card_ids:
        values.update({"delivery plan", "project management", "delivery planner"})
    return any(value and value in normalized for value in values)


def _canonical_task_id(task_id: str) -> str:
    aliases = {
        "BA": "PLAN-01",
        "BUSINESS-ANALYSIS": "PLAN-01",
        "BUSINESS_ANALYSIS": "PLAN-01",
        "REQUIREMENTS": "PLAN-01",
        "REQUIREMENTS-BRIEF": "PLAN-01",
        "ARCH": "PLAN-02",
        "ARCHITECTURE": "PLAN-02",
        "SOLUTION-OVERVIEW": "PLAN-02",
        "PM": "PLAN-03",
        "PROJECT-MANAGEMENT": "PLAN-03",
        "DELIVERY-PLAN": "PLAN-03",
        "TL": "PLAN-04",
        "TEAM-LEAD": "PLAN-04",
        "TEAM_LEAD": "PLAN-04",
        "SPRINT-DELIVERY": "PLAN-04",
    }
    return aliases.get(task_id.upper(), task_id)


def _card_aliases(card: BoardCard) -> set[str]:
    aliases = {card.id}
    if card.id == "PLAN-01":
        aliases.update({"BA", "BUSINESS-ANALYSIS", "BUSINESS_ANALYSIS"})
    elif card.id == "PLAN-02":
        aliases.update({"ARCH", "ARCHITECTURE"})
    elif card.id == "PLAN-03":
        aliases.update({"PM", "PROJECT-MANAGEMENT"})
    elif card.id == "PLAN-04":
        aliases.update({"TL", "TEAM-LEAD", "TEAM_LEAD"})
    return aliases


def _activity_groups_for_card(card: BoardCard, entries: list[str]) -> list[dict[str, object]]:
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        owner = _activity_owner(entry, card.owner)
        grouped.setdefault(owner, []).append(render_markdown(entry))
    ordered = [card.owner, "Quality Reviewer", "Publisher", "Release Reporter", "Delivery Lead"]
    return _ordered_activity_groups(grouped, preferred=ordered)


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


def _activity_owner(entry: str, fallback: str) -> str:
    for owner in (
        "Business Analyst",
        "Solution Architect",
        "Delivery Planner",
        "Delivery Lead",
        "Builder",
        "Quality Reviewer",
        "Publisher",
        "Release Reporter",
        "Coordinator",
    ):
        if owner in entry:
            return owner
    return fallback


def _coordinator_activity_entries(events: list[dict[str, object]]) -> list[str]:
    entries: list[str] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        name = str(event.get("event", ""))
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        target = _started_activity_owner(name, data)
        if not target:
            continue
        timestamp = _display_log_timestamp(str(event.get("timestamp", "")))
        feature = str(
            data.get("feature_id") or data.get("active_feature_id") or data.get("target") or ""
        ).strip()
        suffix = f" ({feature})" if _is_work_item_id(feature) else ""
        dedupe_key = (target, suffix)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        entries.append(f"**{timestamp} - {target} started working{suffix}**")
    return entries


def _started_activity_owner(name: str, data: dict[str, object]) -> str:
    if name == "head_planning_started":
        return "Coordinator"
    if name in {"head_worker_started", "team_lead_worker_started"}:
        return _business_owner_from_event_data(data)
    return {
        "qa_started": "Quality Reviewer",
        "deployment_started": "Publisher",
        "handoff_started": "Release Reporter",
    }.get(name, "")


def _business_owner_from_event_data(data: dict[str, object]) -> str:
    for key in ("target_agent", "agent_id"):
        value = str(data.get(key) or "")
        if value:
            owner = business_agent_label(value)
            if owner != value:
                return owner
    node = str(data.get("node") or "")
    return {
        "business_analyst": "Business Analyst",
        "architecture": "Solution Architect",
        "architect": "Solution Architect",
        "project_management": "Delivery Planner",
        "project_manager": "Delivery Planner",
        "team_lead": "Delivery Lead",
        "fullstack": "Builder",
        "qa": "Quality Reviewer",
        "deployment": "Publisher",
        "handoff": "Release Reporter",
    }.get(node, "")


def _is_user_facing_log_entry(entry: str) -> bool:
    header = entry.split("\n", maxsplit=1)[0]
    hidden_fragments = (
        "Progress saved",
        "Delivery workflow",
        "Workflow step",
        "Coordinator planning started",
        "Coordinator work",
        "Coordinator decision",
        "Requirements started",
        "Requirements completed",
        "Business Analyst started",
        "Business Analyst completed",
        "Architecture started",
        "Architecture completed",
        "Solution approach started",
        "Solution approach completed",
        "Solution Architecture started",
        "Solution Architecture completed",
        "Solution Architect started",
        "Solution Architect completed",
        "Project Management started",
        "Project Management completed",
        "Delivery Planner started",
        "Delivery Planner completed",
        "Delivery Lead sprint",
        "Delivery Lead decision",
        "Delivery Lead work",
        "Builder started",
        "Builder completed",
        "Quality Reviewer started",
        "Quality Reviewer completed",
        "Publisher started",
        "Publisher completed",
        "Release Reporter started",
        "Release Reporter completed",
        "Artifacts written:",
        "Updated files:",
    )
    return not any(fragment in header or fragment in entry for fragment in hidden_fragments)


def _log_sort_key(entry: str) -> str:
    match = re.search(r"\*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", entry)
    if match:
        return match.group(1)
    return entry


def _display_log_timestamp(timestamp: str) -> str:
    if "T" in timestamp:
        date, time = timestamp.split("T", 1)
        return f"{date} {time[:8]}"
    return timestamp[:19] if timestamp else "--:--:--"


def agent_catalog() -> list[dict[str, str]]:
    agents = [
        {
            "name": "Coordinator",
            "initials": "CO",
            "detail": "Plans the delivery journey and delegates work.",
            "model": "OpenAI or Gemini",
            "reasoning": "none",
            "provider": "Planning model",
        },
        {
            "name": "Business Analyst",
            "initials": "BA",
            "detail": "Clarifies scope, users, and acceptance criteria.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Solution Architect",
            "initials": "SA",
            "detail": "Shapes the solution approach and diagrams.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Delivery Planner",
            "initials": "DP",
            "detail": "Builds sprint plan, sequencing, and release path.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Delivery Lead",
            "initials": "DL",
            "detail": "Coordinates build, quality review, publishing, and reporting.",
            "model": "OpenAI or Gemini",
            "reasoning": "none",
            "provider": "Planning model",
        },
        {
            "name": "Builder",
            "initials": "B",
            "detail": "Creates the application slice by slice.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Quality Reviewer",
            "initials": "QR",
            "detail": "Checks product behavior, design quality, and release confidence.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Publisher",
            "initials": "P",
            "detail": "Packages and publishes the generated app.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
        {
            "name": "Release Reporter",
            "initials": "RP",
            "detail": "Prepares the business-facing final report.",
            "model": "gpt-5.3-codex",
            "reasoning": "medium",
            "provider": "Build engine",
        },
    ]
    return [{**agent, "icon": agent_icon_path(agent["name"])} for agent in agents]


def artifact_payload(run_dir: Path, artifact_path: str) -> dict[str, Any]:
    if not is_user_facing_artifact(artifact_path):
        raise ValueError("Artifact is not user-facing")
    return _artifact_payload_for_path(run_dir, artifact_path)


def artifact_payload_by_id(run_dir: Path, artifact_id: str) -> dict[str, Any]:
    record = get_artifact_by_id(run_dir, artifact_id)
    if record is None:
        raise ValueError("Artifact is not user-facing")
    return artifact_payload_for_record(run_dir, record)


def artifact_payload_for_record(run_dir: Path, record: Any) -> dict[str, Any]:
    if record is None or not is_user_facing_artifact_record(record):
        raise ValueError("Artifact is not user-facing")
    return _artifact_payload_for_path(run_dir, record.relative_path)


def _artifact_payload_for_path(run_dir: Path, artifact_path: str) -> dict[str, Any]:
    path = _safe_artifact_path(run_dir, artifact_path)
    kind = artifact_kind(artifact_path)
    if kind == "json":
        try:
            return {"kind": kind, "content": json.dumps(read_json_artifact(path), indent=2)}
        except json.JSONDecodeError:
            return {"kind": "text", "content": read_text_artifact(path)}
    if kind == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        return {"kind": kind, "rows": rows}
    if kind == "markdown":
        return {"kind": kind, "content": render_markdown(read_text_artifact(path))}
    if kind == "html":
        return {"kind": kind, "content": read_text_artifact(path)}
    if kind == "mermaid":
        return {"kind": kind, "content": _normalize_mermaid(read_text_artifact(path))}
    return {"kind": kind, "content": read_text_artifact(path)}


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


def is_technical_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(hint in normalized for hint in TECHNICAL_HINTS) or Path(path).suffix.lower() in {
        ".json",
        ".jsonl",
        ".log",
    }


def is_user_facing_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("handoff/"):
        return normalized.endswith("release-report.html")
    if is_technical_artifact(normalized):
        return False
    if normalized in _USER_FACING_EXACT_ARTIFACTS:
        return True
    if re.match(r"^07-execution-summary(?:-[A-Za-z0-9_-]+)?\.md$", normalized):
        return True
    if re.match(r"^08-qa-report(?:-[A-Za-z0-9_-]+|post-deploy)?\.md$", normalized):
        return True
    if re.match(r"^13-deployment-summary(?:-[A-Za-z0-9_-]+)?\.md$", normalized):
        return True
    if normalized.endswith("/release-report.html"):
        return True
    return False


def user_friendly_artifact_label(label: str, path: str) -> str:
    normalized = path.replace("\\", "/")
    feature = _feature_id_from_path(Path(normalized))
    if normalized.endswith("business-analysis.md"):
        return "Requirements brief"
    if normalized.endswith("architecture.md"):
        return "Solution overview"
    if normalized.endswith("architecture.mmd"):
        return "Architecture diagram"
    if normalized.endswith("release-plan.md"):
        return "Delivery plan"
    if normalized.endswith("risks-and-dependencies.md"):
        return "Risks and dependencies"
    if normalized.endswith("roadmap.csv"):
        return "Delivery roadmap"
    if normalized.endswith("release-report.html"):
        if "/project/final/" in normalized:
            return "Final report"
        sprint = _sprint_from_path(normalized)
        return f"{sprint} report" if sprint else "Report"
    if normalized.startswith("07-execution-summary"):
        return _with_feature("Build summary", feature)
    if normalized.startswith("08-qa-report"):
        return _with_feature("Quality summary", feature)
    if normalized.startswith("13-deployment-summary"):
        return "Deployment summary"
    return label


_USER_FACING_EXACT_ARTIFACTS = {
    "upstream-planning/business-analysis.md",
    "upstream-planning/architecture.md",
    "upstream-planning/architecture.mmd",
    "upstream-planning/project-management/release-plan.md",
    "upstream-planning/project-management/risks-and-dependencies.md",
    "upstream-planning/project-management/roadmap.csv",
}

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


def _artifact_phase(path: str, agent: str) -> str:
    normalized = path.replace("\\", "/")
    if "business-analysis" in normalized:
        return "Requirements"
    if "architecture" in normalized:
        return "Solution Design"
    if "project-management" in normalized or normalized.endswith("roadmap.csv"):
        return "Delivery Planning"
    if "/project/final/" in normalized:
        return "Final Report"
    sprint = _sprint_from_path(normalized)
    if sprint:
        return sprint
    task_id = _task_id_for_artifact(normalized)
    if task_id == "DEPLOY" or "deployment" in normalized:
        return "Sprint 2"
    return business_agent_label(agent)


def _sprint_from_path(path: str) -> str:
    match = re.search(r"sprint-(\d+)", path, flags=re.IGNORECASE)
    return f"Sprint {int(match.group(1))}" if match else ""


def _task_id_for_artifact(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith("upstream-planning/business-analysis.md") or normalized.endswith(
        "business-analysis.md"
    ):
        return "PLAN-01"
    if normalized.endswith("upstream-planning/architecture.md") or normalized.endswith(
        "architecture.md"
    ):
        return "PLAN-02"
    if "upstream-planning/project-management" in normalized or normalized.endswith(
        "release-plan.md"
    ):
        return "PLAN-03"
    if "post-deploy" in normalized or normalized.startswith("13-deployment-summary"):
        return "DEPLOY"
    feature = _feature_id_from_path(Path(normalized))
    if feature:
        return feature
    if "deployment" in normalized:
        return "DEPLOY"
    return ""


def _task_title_map(run_dir: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    for cards in board_cards_for_run(run_dir).values():
        for card in cards:
            titles[card.id] = card.title
    return titles


def _task_sprint_map(run_dir: Path) -> dict[str, str]:
    sprints: dict[str, str] = {}
    for cards in board_cards_for_run(run_dir).values():
        for card in cards:
            sprints[card.id] = sprint_label(card.sprint or "Planning")
    return sprints


def _with_feature(label: str, feature: str) -> str:
    return f"{label} - {feature}" if feature else label


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
    voice_available = bool(os.getenv("SPEECHMATICS_API_KEY"))
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
            "Voice input",
            voice_available,
            "Live dictation is available."
            if voice_available
            else "Browser dictation fallback is available when supported.",
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
        requirements = _fallback_requirement_bullets(cleaned)

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
        r"\bstreamlit\b": "web app",
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
    if "streamlit" in lower or "web app" in lower or "small app" in lower:
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


def _fallback_requirement_bullets(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", _ensure_sentence(text))
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def _summary_sentence(text: str, requirements: list[str]) -> str:
    joined = " ".join(requirements).lower()
    if "small web app" in joined and "three clearly visible buttons" in joined:
        return (
            "Build a small web app with three buttons, visible click results, "
            "deployment, and a simple report with the app link."
        )
    first = _fallback_requirement_bullets(text)[0]
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


def _safe_artifact_path(run_dir: Path, artifact_path: str) -> Path:
    root = run_dir.resolve()
    path = (run_dir / artifact_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Artifact path is outside the run directory")
    return path


def _read_codex_events(run_dir: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for path in _codex_event_paths(run_dir):
        agent_id = _agent_id_from_path(run_dir, path)
        feature_id = _feature_id_from_path(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                agent_id = _agent_id_from_event(event) or agent_id
                event.setdefault("agent_id", agent_id)
                event.setdefault("feature_id", feature_id)
                events.append(event)
    return events


def _codex_event_paths(run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in _codex_event_roots(run_dir):
        if directory.exists():
            paths.extend(sorted(directory.rglob("events.jsonl")))
    return sorted({path for path in paths if path.exists()})


def _codex_event_roots(run_dir: Path) -> list[Path]:
    roots = [
        run_dir / "codex",
        run_dir / "qa" / "codex",
        run_dir / "deployment" / "codex",
        run_dir / "handoff" / "codex",
        run_dir / "team-lead" / "codex-review",
        run_dir / "head" / "codex-review",
    ]
    upstream_dir = run_dir / "upstream-planning"
    roots.append(upstream_dir / "codex")
    if upstream_dir.exists():
        roots.extend(path / "codex" for path in upstream_dir.iterdir() if path.is_dir())
    return roots


def _agent_id_from_path(run_dir: Path, path: Path) -> str:
    normalized = path.relative_to(run_dir).as_posix()
    if normalized.startswith("upstream-planning/business-analyst/codex"):
        return "business-analyst-agent"
    if normalized.startswith("upstream-planning/architect/codex"):
        return "architect-agent"
    if normalized.startswith("upstream-planning/project-manager/codex"):
        return "project-manager-agent"
    if normalized.startswith("upstream-planning/codex/business"):
        return "business-analyst-agent"
    if normalized.startswith("upstream-planning/codex/architecture"):
        return "architect-agent"
    if normalized.startswith("upstream-planning/codex/project"):
        return "project-manager-agent"
    if normalized.startswith("qa/codex"):
        return "qa-codex-agent"
    if normalized.startswith("deployment/codex"):
        return "deployment-codex-agent"
    if normalized.startswith("handoff/codex"):
        return "handoff-codex-agent"
    if normalized.startswith("head/codex-review"):
        return "head-codex-review"
    if normalized.startswith("team-lead/codex-review"):
        return "team-lead-codex-review"
    return "fullstack-agent"


def _agent_id_from_event(event: dict[str, object]) -> str:
    execution_id = str(event.get("codex_execution_id") or "")
    return _agent_id_from_text(execution_id)


def _agent_id_from_text(text: str) -> str:
    normalized = text.lower()
    aliases = {
        "business-analyst-agent": "business-analyst-agent",
        "business-analyst": "business-analyst-agent",
        "architect-agent": "architect-agent",
        "architect": "architect-agent",
        "project-manager-agent": "project-manager-agent",
        "project-manager": "project-manager-agent",
        "qa-codex-agent": "qa-codex-agent",
        "deployment-codex-agent": "deployment-codex-agent",
        "handoff-codex-agent": "handoff-codex-agent",
        "head-codex-review": "head-codex-review",
        "team-lead-codex-review": "team-lead-codex-review",
        "fullstack-agent": "fullstack-agent",
    }
    for alias, agent_id in aliases.items():
        if alias in normalized:
            return agent_id
    return ""


def _feature_id_from_path(path: Path) -> str:
    for part in path.parts:
        if _is_work_item_id(part):
            return part
    filename = path.name
    match = re.search(
        r"\b(F\d+|US-[A-Za-z0-9][A-Za-z0-9_-]*|DEPLOY)\b",
        filename,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(0).removesuffix(".md")
    return ""


def root_path() -> Path:
    return repo_root()
