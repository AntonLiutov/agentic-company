"""Support helpers for the local planning console."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_company.orchestration import DeliveryGraphRuntime
from agentic_company.orchestration.graphs import (
    CONSOLE_DEPLOYMENT_NODE_ORDER,
    CONSOLE_EXECUTION_NODE_ORDER,
)
from agentic_company.platform.artifacts import EXECUTION_REQUEST_ARTIFACT

LOGGER = logging.getLogger(__name__)

_CODEX_THREADS: dict[str, threading.Thread] = {}
_DEPLOYMENT_THREADS: dict[str, threading.Thread] = {}
DEFAULT_ENV_VALUES = {
    "AGENT_LLM_MODEL": "gpt-4.1",
    "COORDINATOR_AGENT_REASONING_EFFORT": "none",
    "SPECIALIST_AGENT_REASONING_EFFORT": "none",
}
DEFAULT_SAMPLE_REQUIREMENTS = "multi-service-task-tracker.md"


@dataclass(slots=True)
class CleanupResult:
    deleted: int
    skipped: list[str]


@dataclass(slots=True)
class FeatureProgress:
    feature_id: str
    title: str
    status: str
    delivery_order: int
    active: bool
    repair_attempts: int
    owner: str
    sprint_id: str = ""
    lane: str = ""
    assigned_agent: str = ""
    story_points: int = 0
    artifact_count: int = 0


@dataclass(slots=True)
class DeploymentTarget:
    label: str
    url: str
    service: str = ""


@dataclass(slots=True)
class TeamLeadStep:
    step: int
    tool: str
    target: str
    reason: str
    status: str


@dataclass(slots=True)
class CurrentWork:
    stage: str
    feature_id: str
    title: str
    status: str
    lane: str
    owner: str
    assigned_agent: str
    last_tool: str
    last_target: str
    last_reason: str
    last_status: str


@dataclass(slots=True)
class DeliveryOverview:
    run_id: str
    stage: str
    status: str
    active_feature_id: str | None
    features: list[FeatureProgress]
    qa_status: str
    deployment_status: str
    handoff_status: str
    topology_summary: str
    deployment_targets: list[DeploymentTarget]
    blockers: list[str]
    team_lead_steps: list[TeamLeadStep]
    current_work: CurrentWork | None

    @property
    def completed_feature_count(self) -> int:
        return sum(1 for feature in self.features if feature.status in _FEATURE_DONE_STATUSES)

    @property
    def total_feature_count(self) -> int:
        return len(self.features)


def console_status_label(value: str) -> str:
    """Format graph/status tokens for the operator console."""

    if not value:
        return "Pending"
    return value.replace("_", " ").strip().title().replace("Qa", "QA")


def team_lead_step_rows(steps: Sequence[TeamLeadStep]) -> list[dict[str, object]]:
    """Return every Team Lead decision row for the Overview table."""

    return [
        {
            "Step": step.step,
            "Tool": _team_lead_tool_label(step.tool),
            "Target": step.target or "-",
            "Result": console_status_label(step.status or "pending"),
            "Reason": step.reason,
        }
        for step in steps
    ]


def _team_lead_tool_label(tool: str) -> str:
    if not tool:
        return ""
    return console_status_label(tool.removeprefix("run_"))


ArtifactSpec = tuple[str, str, str]
ArtifactGroup = tuple[str, str, list[ArtifactSpec]]

UPSTREAM_PLANNING_ARTIFACTS = [
    ("upstream-planning/business-analysis.md", "Business analysis brief", "Business Analyst"),
    ("upstream-planning/business-analysis.json", "Business analysis data", "Business Analyst"),
    (
        "upstream-planning/business-analysis-request.json",
        "Business analysis request",
        "Business Analyst",
    ),
    ("upstream-planning/architecture.md", "Architecture brief", "Architect"),
    ("upstream-planning/architecture.json", "Architecture data", "Architect"),
    ("upstream-planning/architecture.mmd", "Architecture diagram", "Architect"),
    (
        "upstream-planning/architecture-request.json",
        "Architecture request",
        "Architect",
    ),
    (
        "upstream-planning/project-management/release-plan.md",
        "Release plan",
        "Project Manager",
    ),
    (
        "upstream-planning/project-management/release-plan.json",
        "Release plan data",
        "Project Manager",
    ),
    (
        "upstream-planning/project-management/candidate-feature-queue.json",
        "Candidate feature queue",
        "Project Manager",
    ),
    (
        "upstream-planning/project-management/risks-and-dependencies.md",
        "Risks and dependencies",
        "Project Manager",
    ),
    (
        "upstream-planning/project-management/roadmap.csv",
        "Roadmap table",
        "Project Manager",
    ),
    (
        "upstream-planning/project-management-request.json",
        "Project management request",
        "Project Manager",
    ),
]

HEAD_ARTIFACTS = [
    ("head/planning-history.json", "Planning coordination history", "Head Agent"),
    ("head/result.json", "Planning coordination result", "Head Agent"),
]

EXECUTION_ARTIFACTS = [
    ("07-execution-summary.md", "Execution summary", "Fullstack Agent"),
    ("08-qa-report.md", "QA report", "QA Agent"),
]

TEAM_LEAD_ARTIFACTS = [
    ("team-lead/sprint-01-plan.json", "Sprint plan", "Team Lead Agent"),
    ("team-lead/sprint-01-result.json", "Sprint result", "Team Lead Agent"),
]

DEPLOYMENT_ARTIFACTS = [
    ("13-deployment-summary.md", "Deployment summary", "Deployment Agent"),
]

HANDOFF_ARTIFACTS = [
    ("handoff/release-report.html", "Release report", "Documentation / Handoff Agent"),
    (
        "handoff/project/final/release-report.html",
        "Final project release report",
        "Documentation / Handoff Agent",
    ),
]

DEPLOYMENT_DETAIL_ARTIFACTS = [
    ("deployment/result.json", "Deployment result", "Deployment Agent"),
    ("11-deployment-plan.md", "Deployment plan", "Deployment Agent"),
    ("11-deployment-plan.json", "Deployment plan data", "Deployment Agent"),
    ("12-deployment-request.md", "Deployment request", "Deployment Agent"),
    ("12-deployment-request.json", "Deployment request data", "Deployment Agent"),
    (
        "deployment/browser/post-deploy-chat-transcript.json",
        "Post-deploy chat transcript",
        "Deployment Agent",
    ),
]

QA_DIAGNOSTIC_ARTIFACTS = [
    ("10-fix-request.md", "Fix request", "QA Agent"),
    ("10-fix-request.json", "Fix request data", "QA Agent"),
    ("qa/results.json", "QA results", "QA Agent"),
    ("qa/docker/build-summary.json", "Docker build summary", "QA Agent"),
]

CODEX_DIAGNOSTIC_ARTIFACTS = [
    ("codex/prompt.md", "Codex prompt", "Fullstack Agent"),
]

ARTIFACTS = [
    *UPSTREAM_PLANNING_ARTIFACTS,
    *HEAD_ARTIFACTS,
    *EXECUTION_ARTIFACTS,
    *DEPLOYMENT_ARTIFACTS,
    *HANDOFF_ARTIFACTS,
]

ARTIFACT_GROUPS: list[ArtifactGroup] = [
    (
        "Business Analyst",
        "Business requirements, scope, acceptance criteria, and open questions.",
        UPSTREAM_PLANNING_ARTIFACTS,
    ),
    (
        "Team Lead",
        "AgentExecutor decisions, selected tools, sprint state, and sprint outcome.",
        TEAM_LEAD_ARTIFACTS,
    ),
    (
        "Build And QA",
        "Engineer execution and QA result.",
        EXECUTION_ARTIFACTS,
    ),
    (
        "Deployment",
        "Final Deployment Agent result.",
        DEPLOYMENT_ARTIFACTS,
    ),
    (
        "Handoff",
        "Final handoff after deployment and deployed-app QA.",
        HANDOFF_ARTIFACTS,
    ),
]

DIAGNOSTIC_ARTIFACT_GROUPS: list[ArtifactGroup] = [
    (
        "QA Evidence",
        "Structured QA results, browser evidence, Docker build evidence, and repair requests.",
        QA_DIAGNOSTIC_ARTIFACTS,
    ),
    (
        "Deployment Internals",
        "Deployment Codex result, plan/request files, and runtime evidence.",
        DEPLOYMENT_DETAIL_ARTIFACTS,
    ),
    (
        "Codex Input",
        "Prompt sent to the Fullstack Agent.",
        CODEX_DIAGNOSTIC_ARTIFACTS,
    ),
]

RUNTIME_BY_AGENT = {
    "delivery-graph": "L1 LangGraph",
    "head-agent": "L4 AgentExecutor",
    "head-codex-review": "L6 Codex Review",
    "business-analyst-agent": "L6 Codex Business Analyst",
    "architect-agent": "L6 Codex Architect",
    "project-manager-agent": "L6 Codex Project Manager",
    "team-lead-agent": "L4 AgentExecutor",
    "fullstack-agent": "L6 Codex Agent",
    "qa-agent": "L6 Codex QA Agent",
    "qa-codex-agent": "L6 Codex QA Agent",
    "deployment-codex-agent": "L6 Codex Deployment Agent",
    "documentation-handoff-agent": "L6 Codex Handoff Agent",
    "handoff-codex-agent": "L6 Codex Handoff Agent",
    "deployment-agent": "L6 Codex Deployment Agent",
}

AGENT_ARTIFACT_LABELS = {
    "head-agent": "Head Agent",
    "head-codex-review": "Head Agent",
    "business-analyst-agent": "Business Analyst",
    "architect-agent": "Architect",
    "project-manager-agent": "Project Manager",
    "team-lead-agent": "Team Lead Agent",
    "fullstack-agent": "Fullstack Agent",
    "qa-agent": "QA Agent",
    "qa-codex-agent": "QA Agent",
    "deployment-codex-agent": "Deployment Agent",
    "deployment-agent": "Deployment Agent",
    "documentation-handoff-agent": "Documentation / Handoff Agent",
    "handoff-codex-agent": "Documentation / Handoff Agent",
}

AGENT_ARTIFACT_ORDER = [
    "Documentation / Handoff Agent",
    "Head Agent",
    "Business Analyst",
    "Architect",
    "Project Manager",
    "Team Lead Agent",
    "Fullstack Agent",
    "QA Agent",
    "Deployment Agent",
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def list_sample_requirements(root: Path | None = None) -> list[Path]:
    base = root or repo_root()
    samples_dir = base / "examples" / "requirements"
    if not samples_dir.exists():
        return []
    return sorted(samples_dir.glob("*.md"))


def sample_requirements_path(
    root: Path | None = None,
    filename: str = DEFAULT_SAMPLE_REQUIREMENTS,
) -> Path:
    base = root or repo_root()
    return base / "examples" / "requirements" / filename


def load_sample_requirements(
    root: Path | None = None,
    filename: str = DEFAULT_SAMPLE_REQUIREMENTS,
) -> str:
    return sample_requirements_path(root, filename).read_text(encoding="utf-8")


def create_console_run(
    requirements_text: str,
    output_root: Path | None = None,
    *,
    run_id_prefix: str = "console",
) -> Path:
    output_base = output_root or repo_root() / "runs"
    run_id = _run_id(run_id_prefix)
    output_dir = output_base / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    requirements_path = output_dir / "00-requirements.md"
    requirements_path.write_text(requirements_text.strip() + "\n", encoding="utf-8")
    LOGGER.info("Created console run shell run_id=%s output_dir=%s", run_id, output_dir)
    return output_dir


def run_codex_execution(run_dir: Path) -> str:
    LOGGER.info("Running console execution graph synchronously run_dir=%s", run_dir)
    state = _run_console_execution_graph(run_dir)
    LOGGER.info(
        "Console execution graph finished status=%s run_dir=%s",
        state["status"],
        run_dir,
    )
    return _execution_summary_text(run_dir) or f"Graph execution finished: {state['status']}"


def start_codex_execution(run_dir: Path) -> int:
    if codex_execution_running(run_dir) or execution_completed(run_dir):
        LOGGER.info("Codex execution start skipped run_dir=%s", run_dir)
        return 0

    status_path = _codex_status_path(run_dir)
    status_path.write_text("starting\n", encoding="utf-8")
    thread = threading.Thread(
        target=_run_codex_in_thread,
        args=(run_dir,),
        name=f"codex-execution-{run_dir.name}",
        daemon=True,
    )
    _CODEX_THREADS[str(run_dir)] = thread
    thread.start()
    status_path.write_text(f"running\nthread={thread.name}\n", encoding="utf-8")
    LOGGER.info("Started Codex execution thread=%s run_dir=%s", thread.name, run_dir)
    return thread.ident or 0


def start_azure_deployment(run_dir: Path) -> int:
    if azure_deployment_running(run_dir) or deployment_completed(run_dir):
        LOGGER.info("Deployment start skipped run_dir=%s", run_dir)
        return 0

    status_path = _deployment_status_path(run_dir)
    status_path.write_text("starting\n", encoding="utf-8")
    thread = threading.Thread(
        target=_run_deployment_in_thread,
        args=(run_dir,),
        name=f"azure-deployment-{run_dir.name}",
        daemon=True,
    )
    _DEPLOYMENT_THREADS[str(run_dir)] = thread
    thread.start()
    status_path.write_text(f"running\nthread={thread.name}\n", encoding="utf-8")
    LOGGER.info("Started deployment thread=%s run_dir=%s", thread.name, run_dir)
    return thread.ident or 0


def codex_execution_running(run_dir: Path) -> bool:
    if _summary_has_failed(run_dir):
        return False
    status_text = _read_codex_status(run_dir)
    if status_text.startswith(("failed", "completed", "stopped")):
        return False
    thread = _CODEX_THREADS.get(str(run_dir))
    if thread and thread.is_alive():
        return True
    if status_text.startswith(("starting", "running")):
        if _execution_status_is_stale(run_dir):
            LOGGER.info("Ignoring stale Codex running status run_dir=%s", run_dir)
            return False
        state = _read_delivery_state(run_dir)
        if _delivery_execution_terminal(state):
            return _delivery_terminal_requires_review(state) and not review_completed(run_dir)
        return True
    if execution_completed(run_dir):
        return False
    if (run_dir / "codex" / "execution.log").exists() and not (
        run_dir / "07-execution-summary.md"
    ).exists():
        return True
    if _feature_codex_log_paths(run_dir):
        return True
    return False


def request_codex_execution_stop(run_dir: Path) -> Path:
    """Request a cooperative stop for a web-console delivery run."""

    run_dir.mkdir(parents=True, exist_ok=True)
    stop_path = run_dir / ".stop-requested"
    stop_path.write_text("stopped_by_user\n", encoding="utf-8")
    _codex_status_path(run_dir).write_text("stopped\nreason=user_requested\n", encoding="utf-8")
    return stop_path


def _execution_status_is_stale(run_dir: Path) -> bool:
    if _CODEX_THREADS.get(str(run_dir)):
        return False
    latest_activity = _latest_execution_activity_mtime(run_dir)
    if latest_activity <= 0:
        return False
    return time.time() - latest_activity > 120


def _latest_execution_activity_mtime(run_dir: Path) -> float:
    candidates = [
        run_dir / ".codex-execution.status",
        run_dir / ".delivery-state.json",
        run_dir / "events.jsonl",
    ]
    for directory in (
        run_dir / "upstream-planning",
        run_dir / "codex",
        run_dir / "qa",
        run_dir / "deployment",
        run_dir / "handoff",
        run_dir / "team-lead",
        run_dir / "head",
    ):
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    mtimes = []
    for path in candidates:
        try:
            mtimes.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(mtimes, default=0)


def azure_deployment_running(run_dir: Path) -> bool:
    thread = _DEPLOYMENT_THREADS.get(str(run_dir))
    if thread and thread.is_alive():
        return True
    status_text = _read_deployment_status(run_dir)
    if status_text.startswith(("failed", "completed", "stopped")):
        return False
    if status_text.startswith(("starting", "running")):
        return not deployment_completed(run_dir)
    return False


def deployment_completed(run_dir: Path) -> bool:
    deployment_result = _read_optional_json(run_dir / "deployment" / "result.json")
    if str(deployment_result.get("status") or "") == "deployed":
        return True
    summary_path = run_dir / "13-deployment-summary.md"
    if not summary_path.exists():
        return False
    return "Status: deployed" in read_text_artifact(summary_path)


def review_completed(run_dir: Path) -> bool:
    qa_dir = run_dir / "qa"
    if not qa_dir.exists():
        return False
    return (qa_dir / "results.json").exists() or any(qa_dir.glob("results-*.json"))


def _execution_summary_paths(run_dir: Path) -> list[Path]:
    paths = [run_dir / "07-execution-summary.md"]
    paths.extend(sorted(run_dir.glob("07-execution-summary-*.md")))
    return [path for path in paths if path.exists()]


def _feature_codex_log_paths(run_dir: Path) -> list[Path]:
    codex_dir = run_dir / "codex"
    if not codex_dir.exists():
        return []
    return sorted(codex_dir.rglob("execution.log"))


def _read_delivery_state(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / ".delivery-state.json"
    if not state_path.exists():
        return {}
    for attempt in range(3):
        try:
            raw_state = state_path.read_text(encoding="utf-8")
            return json.loads(raw_state) if raw_state.strip() else {}
        except PermissionError:
            if attempt == 2:
                LOGGER.warning("Delivery state is temporarily locked run_dir=%s", run_dir)
                return {}
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            LOGGER.warning(
                "Delivery state is temporarily unavailable run_dir=%s error=%s",
                run_dir,
                exc,
            )
            return {}
        except json.JSONDecodeError:
            LOGGER.warning("Delivery state is temporarily unreadable run_dir=%s", run_dir)
            return {}


def execution_completed(run_dir: Path) -> bool:
    state = _read_delivery_state(run_dir)
    if _delivery_execution_terminal(state):
        return True
    summaries = _execution_summary_paths(run_dir)
    if not summaries:
        return False
    return all("Status: codex failed" not in read_text_artifact(path) for path in summaries)


def workflow_should_refresh(run_dir: Path, *, execution_is_running: bool) -> bool:
    """Return whether the console should continue refreshing a running workflow."""

    if execution_is_running:
        return True

    events = read_events(run_dir)
    execution_started = any(event.get("event") == "execution_started" for event in events)
    terminal_events = {
        "delivery_graph_completed",
        "head_agent_completed",
        "head_delivery_completed",
        "head_planning_completed",
        "head_planning_blocked",
        "business_analysis_blocked",
        "architecture_blocked",
        "project_management_blocked",
        "execution_failed",
    }
    terminal_seen = any(event.get("event") in terminal_events for event in events)
    return execution_started and not terminal_seen


def _delivery_execution_terminal(state: dict[str, Any]) -> bool:
    status = str(state.get("status", ""))
    return status in {
        "head_planning_completed",
        "head_delivery_completed",
        "head_planning_blocked",
        "team_lead_sprint_blocked",
        "fullstack_feature_queue_completed_downstream_paused",
        "feature_queue_qa_completed_downstream_paused",
    } or status.startswith("deployment_")


def _delivery_terminal_requires_review(state: dict[str, Any]) -> bool:
    return str(state.get("status", "")) in {
        "fullstack_feature_queue_completed_downstream_paused",
        "feature_queue_qa_completed_downstream_paused",
    }


def _summary_has_failed(run_dir: Path) -> bool:
    return any(
        "Status: codex failed" in read_text_artifact(path)
        for path in _execution_summary_paths(run_dir)
    )


def clear_console_runs(output_root: Path | None = None) -> CleanupResult:
    runs_root = output_root or repo_root() / "runs"
    if not runs_root.exists():
        return CleanupResult(deleted=0, skipped=[])

    deleted = 0
    skipped: list[str] = []
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir() or not run_dir.name.startswith("console-"):
            continue
        try:
            shutil.rmtree(run_dir)
        except OSError as exc:
            skipped.append(f"{run_dir.name}: {exc}")
            LOGGER.warning("Skipped console run cleanup run_dir=%s error=%s", run_dir, exc)
        else:
            deleted += 1
            LOGGER.info("Deleted console run run_dir=%s", run_dir)
    return CleanupResult(deleted=deleted, skipped=skipped)


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    event_path = run_dir / "events.jsonl"
    if not event_path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in event_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        agent_id = str(event.get("agent_id", ""))
        event["runtime"] = RUNTIME_BY_AGENT.get(agent_id, "Unknown Runtime")
        events.append(event)
    return events


def read_json_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def read_text_artifact(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_FEATURE_DONE_STATUSES = {
    "qa_passed",
    "done",
    "delivered",
    "deployed",
    "handoff_ready",
}


def delivery_overview_for_run(run_dir: Path) -> DeliveryOverview:
    """Return a compact, UI-friendly delivery overview for a console run."""

    state = _read_delivery_state(run_dir)
    deployment_result = _read_optional_json(run_dir / "deployment" / "result.json")
    board_features = _feature_progress_from_board(state)
    feature_queue = _feature_queue_from_state_or_plan(run_dir, state)
    feature_statuses = _as_dict(state.get("feature_statuses", {}))
    completed_feature_ids = {
        str(feature_id) for feature_id in state.get("completed_feature_ids", []) if feature_id
    }
    active_feature_id = _optional_str(state.get("active_feature_id"))
    repair_attempts = _as_dict(state.get("feature_repair_attempts", {}))

    features = board_features or [
        _feature_progress(
            feature,
            feature_statuses=feature_statuses,
            completed_feature_ids=completed_feature_ids,
            active_feature_id=active_feature_id,
            repair_attempts=repair_attempts,
        )
        for feature in feature_queue
    ]
    deployment_status = str(state.get("deployment_status") or deployment_result.get("status") or "")
    _apply_deployment_completion_to_features(features, deployment_status=deployment_status)
    handoff_status = _handoff_status(run_dir, state)
    _apply_handoff_completion_to_features(features, handoff_status=handoff_status)
    qa_status = str(state.get("qa_status") or _feature_queue_qa_status(features, run_dir))
    _apply_terminal_success_completion_to_features(
        features,
        state=state,
        qa_status=qa_status,
        deployment_status=deployment_status,
        handoff_status=handoff_status,
    )
    _apply_current_worker_to_features(features, run_dir, state)
    team_lead_steps = _team_lead_steps(run_dir)

    return DeliveryOverview(
        run_id=str(state.get("run_id") or run_dir.name),
        stage=_current_stage_for_run(run_dir, state),
        status=str(state.get("status") or "planning_ready"),
        active_feature_id=active_feature_id,
        features=features,
        qa_status=qa_status,
        deployment_status=deployment_status,
        handoff_status=handoff_status,
        topology_summary=str(deployment_result.get("topology_summary") or ""),
        deployment_targets=_deployment_targets(deployment_result),
        blockers=[str(blocker) for blocker in state.get("blockers", []) if blocker],
        team_lead_steps=team_lead_steps,
        current_work=_current_work(features, team_lead_steps, run_dir, state),
    )


def _team_lead_steps(run_dir: Path) -> list[TeamLeadStep]:
    history_dir = run_dir / "team-lead"
    histories = sorted(history_dir.glob("*-history.json")) if history_dir.exists() else []
    normalized: list[TeamLeadStep] = []
    for history_path in histories:
        history = _read_optional_json(history_path)
        steps = history.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            normalized.append(
                TeamLeadStep(
                    step=len(normalized) + 1,
                    tool=str(step.get("tool") or ""),
                    target=str(step.get("target") or step.get("active_feature_id") or ""),
                    reason=str(step.get("reason") or ""),
                    status=str(step.get("result_status") or ""),
                )
            )
    event_steps = _team_lead_steps_from_events(run_dir)
    if len(event_steps) > len(normalized):
        return event_steps
    return normalized


def _team_lead_steps_from_events(run_dir: Path) -> list[TeamLeadStep]:
    steps: list[TeamLeadStep] = []
    for event in read_events(run_dir):
        event_name = str(event.get("event") or "")
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        if event_name == "team_lead_decision":
            decision = data.get("decision", {})
            if not isinstance(decision, dict):
                continue
            steps.append(
                TeamLeadStep(
                    step=len(steps) + 1,
                    tool=str(decision.get("tool") or ""),
                    target=str(decision.get("target") or ""),
                    reason=str(decision.get("reason") or ""),
                    status="",
                )
            )
        elif event_name == "team_lead_tool_completed" and steps:
            for index in range(len(steps) - 1, -1, -1):
                step = steps[index]
                if step.status:
                    continue
                steps[index] = TeamLeadStep(
                    step=step.step,
                    tool=step.tool,
                    target=step.target,
                    reason=step.reason,
                    status=str(data.get("status") or ""),
                )
                break
    return steps


def _current_work(
    features: list[FeatureProgress],
    steps: list[TeamLeadStep],
    run_dir: Path,
    state: dict[str, Any],
) -> CurrentWork | None:
    active_stage = _current_stage_for_run(run_dir, state)
    active_feature_id = _current_feature_id_for_state(state, features, active_stage)
    feature = _active_feature_progress(features, active_feature_id)
    last_step = steps[-1] if steps else None

    if not feature and not last_step and not active_stage:
        return None
    status = feature.status if feature else str(state.get("status") or "")
    lane = feature.lane if feature else ""
    if feature and _feature_is_current_worker_target(feature, state, active_stage):
        if status in {"", "pending", "assigned"}:
            status = "in_progress"
        if not lane or lane == "todo":
            lane = "doing"
    return CurrentWork(
        stage=active_stage,
        feature_id=feature.feature_id if feature else active_feature_id or "",
        title=feature.title if feature else "",
        status=status,
        lane=lane,
        owner=feature.owner if feature else "",
        assigned_agent=_current_work_assigned_agent(feature, state, active_stage),
        last_tool=last_step.tool if last_step else "",
        last_target=last_step.target if last_step else "",
        last_reason=last_step.reason if last_step else "",
        last_status=last_step.status if last_step else "",
    )


def _apply_current_worker_to_features(
    features: list[FeatureProgress],
    run_dir: Path,
    state: dict[str, Any],
) -> None:
    active_stage = _current_stage_for_run(run_dir, state)
    active_feature_id = _current_feature_id_for_state(state, features, active_stage)
    if not active_feature_id:
        return
    for feature in features:
        is_active = feature.feature_id == active_feature_id
        if not is_active:
            continue
        if feature.status in _FEATURE_DONE_STATUSES:
            feature.active = False
            continue
        if not _feature_is_current_worker_target(feature, state, active_stage):
            continue
        feature.active = True
        if feature.status in {"", "pending", "assigned"}:
            feature.status = "in_progress"
        if not feature.lane or feature.lane == "todo":
            feature.lane = "doing"
        feature.assigned_agent = _current_work_assigned_agent(feature, state, active_stage)


def _current_feature_id_for_state(
    state: dict[str, Any],
    features: list[FeatureProgress],
    active_stage: str,
) -> str | None:
    active_feature_id = _optional_str(state.get("active_feature_id"))
    if active_feature_id:
        return active_feature_id

    correlation_id = _optional_str(state.get("agent_call_correlation_id"))
    if correlation_id and any(feature.feature_id == correlation_id for feature in features):
        return correlation_id

    agent_id = str(state.get("agent_execution_agent_id") or "")
    if agent_id == "deployment-agent" or active_stage == "deployment":
        return _first_feature_id(features, owner="deployment-agent", preferred_id="DEPLOY")
    if agent_id == "documentation-handoff-agent" or active_stage == "handoff":
        return _first_feature_id(features, owner="documentation-handoff-agent")
    return None


def _first_feature_id(
    features: list[FeatureProgress],
    *,
    owner: str,
    preferred_id: str = "",
) -> str | None:
    if preferred_id:
        for feature in features:
            if feature.feature_id == preferred_id:
                return feature.feature_id
    for feature in features:
        if feature.owner == owner and feature.status not in _FEATURE_DONE_STATUSES:
            return feature.feature_id
    return None


def _feature_is_current_worker_target(
    feature: FeatureProgress,
    state: dict[str, Any],
    active_stage: str,
) -> bool:
    correlation_id = str(state.get("agent_call_correlation_id") or "")
    agent_id = str(state.get("agent_execution_agent_id") or "")
    if correlation_id and feature.feature_id == correlation_id:
        return True
    if agent_id and feature.owner == agent_id:
        return True
    return (active_stage == "deployment" and feature.owner == "deployment-agent") or (
        active_stage == "handoff" and feature.owner == "documentation-handoff-agent"
    )


def _current_work_assigned_agent(
    feature: FeatureProgress | None,
    state: dict[str, Any],
    active_stage: str,
) -> str:
    execution_agent = str(state.get("agent_execution_agent_id") or "")
    if feature and _feature_is_current_worker_target(feature, state, active_stage):
        return execution_agent or feature.owner or feature.assigned_agent
    return feature.assigned_agent if feature else execution_agent


def _active_feature_progress(
    features: list[FeatureProgress],
    active_feature_id: str | None,
) -> FeatureProgress | None:
    if active_feature_id:
        for feature in features:
            if feature.feature_id == active_feature_id:
                return feature
    for feature in features:
        if feature.active:
            return feature
    for feature in features:
        if feature.status in {"assigned", "in_progress", "implemented", "in_qa", "qa_failed"}:
            return feature
    return None


def _feature_queue_from_state_or_plan(
    run_dir: Path,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    state_queue = state.get("feature_queue", [])
    if isinstance(state_queue, list) and state_queue:
        return [item for item in state_queue if isinstance(item, dict)]

    candidate_queue = state.get("candidate_feature_queue", [])
    if isinstance(candidate_queue, list) and candidate_queue:
        return [item for item in candidate_queue if isinstance(item, dict)]

    pm_queue = _read_optional_json_value(
        run_dir / "upstream-planning" / "project-management" / "candidate-feature-queue.json"
    )
    if isinstance(pm_queue, list):
        return [item for item in pm_queue if isinstance(item, dict)]
    return []


def _feature_progress(
    feature: dict[str, Any],
    *,
    feature_statuses: dict[str, Any],
    completed_feature_ids: set[str],
    active_feature_id: str | None,
    repair_attempts: dict[str, Any],
) -> FeatureProgress:
    feature_id = str(feature.get("id") or "")
    raw_status = str(feature_statuses.get(feature_id) or "")
    if not raw_status and feature_id in completed_feature_ids:
        raw_status = "qa_passed"
    if not raw_status:
        raw_status = "active" if feature_id == active_feature_id else "pending"

    return FeatureProgress(
        feature_id=feature_id,
        title=str(feature.get("title") or feature.get("user_value") or "Untitled feature"),
        status=raw_status,
        delivery_order=_int_value(feature.get("delivery_order"), default=0),
        active=feature_id == active_feature_id,
        repair_attempts=_int_value(repair_attempts.get(feature_id), default=0),
        owner=str(feature.get("suggested_owner_agent") or ""),
        sprint_id=str(feature.get("sprint_id") or ""),
        story_points=_int_value(feature.get("story_points"), default=0),
    )


def _feature_progress_from_board(state: dict[str, Any]) -> list[FeatureProgress]:
    board = state.get("work_board", {})
    if not isinstance(board, dict):
        return []
    items = board.get("items", [])
    if not isinstance(items, list):
        return []

    features: list[FeatureProgress] = []
    repair_attempts = _as_dict(state.get("feature_repair_attempts", {}))
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        artifact_refs = item.get("artifact_refs", [])
        features.append(
            FeatureProgress(
                feature_id=item_id,
                title=str(item.get("title") or "Untitled feature"),
                status=str(item.get("status") or "pending"),
                delivery_order=_int_value(item.get("delivery_order"), default=0),
                active=bool(item.get("active")),
                repair_attempts=_int_value(repair_attempts.get(item_id), default=0),
                owner=str(item.get("owner_agent") or ""),
                sprint_id=str(item.get("sprint_id") or ""),
                lane=str(item.get("lane") or ""),
                assigned_agent=_current_assigned_agent(item),
                story_points=_int_value(item.get("story_points"), default=0),
                artifact_count=len(artifact_refs) if isinstance(artifact_refs, list) else 0,
            )
        )
    return sorted(features, key=lambda feature: feature.delivery_order)


def _apply_deployment_completion_to_features(
    features: list[FeatureProgress],
    *,
    deployment_status: str,
) -> None:
    if deployment_status != "deployed":
        return
    for feature in features:
        owner_hint = f"{feature.owner} {feature.assigned_agent}".lower()
        if "deployment-agent" not in owner_hint:
            continue
        feature.status = "deployed"
        feature.lane = "done"
        feature.active = False


def _apply_handoff_completion_to_features(
    features: list[FeatureProgress],
    *,
    handoff_status: str,
) -> None:
    if handoff_status not in {"ready", "handoff_ready", "complete", "completed"}:
        return
    for feature in features:
        if not _is_handoff_feature(feature):
            continue
        if feature.status in {"blocked", "failed"}:
            continue
        feature.status = "handoff_ready"
        feature.lane = "done"
        feature.active = False
        feature.assigned_agent = ""


def _apply_terminal_success_completion_to_features(
    features: list[FeatureProgress],
    *,
    state: dict[str, Any],
    qa_status: str,
    deployment_status: str,
    handoff_status: str,
) -> None:
    if state.get("blockers"):
        return
    terminal_status = str(state.get("status") or "")
    handoff_ready = handoff_status in {"ready", "handoff_ready", "complete", "completed"}
    if not (
        terminal_status == "head_delivery_completed"
        and qa_status == "passed"
        and deployment_status == "deployed"
        and handoff_ready
    ):
        return
    for feature in features:
        if feature.status in _FEATURE_DONE_STATUSES or feature.status in {"blocked", "failed"}:
            continue
        feature.status = "qa_passed"
        feature.lane = "done"
        feature.active = False
        feature.assigned_agent = ""


def _is_handoff_feature(feature: FeatureProgress) -> bool:
    text = f"{feature.feature_id} {feature.title} {feature.owner}".lower()
    return "handoff" in text or "completion report" in text


def _current_assigned_agent(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "")
    if (
        not item.get("active")
        or status in _FEATURE_DONE_STATUSES
        or status in {"blocked", "failed"}
    ):
        return ""
    return str(item.get("assigned_agent") or "")


def _feature_queue_qa_status(features: list[FeatureProgress], run_dir: Path) -> str:
    if not features:
        return _qa_status_from_artifact(run_dir / "qa" / "results.json")
    if any(feature.status.startswith(("qa_failed", "blocked", "failed")) for feature in features):
        return "failed"
    if all(feature.status in _FEATURE_DONE_STATUSES for feature in features):
        return "passed"
    return ""


def _current_stage_for_run(run_dir: Path, state: dict[str, Any]) -> str:
    active = _active_stage_from_events(read_events(run_dir))
    if active:
        return active
    return str(state.get("stage") or _stage_from_artifacts(run_dir))


def _active_stage_from_events(events: list[dict[str, Any]]) -> str:
    active: list[tuple[tuple[str, str], str]] = []
    for event in events:
        start = _stage_start(event)
        if start:
            active.append(start)
            continue
        stop = _stage_stop(event)
        if stop:
            active = [item for item in active if item[0] != stop]
    return active[-1][1] if active else ""


def _stage_start(event: dict[str, Any]) -> tuple[tuple[str, str], str] | None:
    name = str(event.get("event") or "")
    data = event.get("data", {})
    payload = data if isinstance(data, dict) else {}

    if name == "delivery_graph_node_started":
        node = str(payload.get("node") or "")
        return (("graph", node), _stage_token(node)) if node else None
    if name == "head_worker_started":
        node = str(payload.get("node") or "")
        return (("head_worker", node), _stage_token(node)) if node else None
    if name == "team_lead_worker_started":
        node = str(payload.get("node") or "")
        return (("team_lead_worker", node), _stage_token(node)) if node else None

    stage = _stage_from_started_event(name)
    if stage:
        return ((str(event.get("agent_id") or ""), stage), stage)
    return None


def _stage_stop(event: dict[str, Any]) -> tuple[str, str] | None:
    name = str(event.get("event") or "")
    data = event.get("data", {})
    payload = data if isinstance(data, dict) else {}

    if name in {
        "delivery_graph_node_completed",
        "delivery_graph_node_failed",
        "delivery_graph_node_skipped",
    }:
        node = str(payload.get("node") or "")
        return ("graph", node) if node else None
    if name == "head_worker_completed":
        node = str(payload.get("node") or "")
        return ("head_worker", node) if node else None
    if name == "team_lead_worker_completed":
        node = str(payload.get("node") or "")
        return ("team_lead_worker", node) if node else None

    stage = _stage_from_completed_event(name)
    if stage:
        return (str(event.get("agent_id") or ""), stage)
    return None


def _stage_from_started_event(event_name: str) -> str:
    if event_name == "execution_started":
        return "fullstack"
    for prefix in (
        "business_analysis",
        "architecture",
        "project_management",
        "team_lead",
        "qa",
        "deployment",
        "handoff",
    ):
        if event_name == f"{prefix}_started" or event_name == f"{prefix}_codex_started":
            return prefix
    return ""


def _stage_from_completed_event(event_name: str) -> str:
    if event_name == "execution_completed":
        return "fullstack"
    for prefix in (
        "business_analysis",
        "architecture",
        "project_management",
        "team_lead",
        "qa",
        "deployment",
        "handoff",
    ):
        if event_name in {
            f"{prefix}_completed",
            f"{prefix}_codex_completed",
            f"{prefix}_blocked",
        }:
            return prefix
    return ""


def _stage_token(node: str) -> str:
    return {
        "business_analyst": "business_analysis",
        "architecture": "architecture",
        "project_management": "project_management",
        "team_lead": "team_lead",
        "fullstack": "fullstack",
        "qa": "qa",
        "deployment": "deployment",
        "handoff": "handoff",
        "head": "head",
    }.get(node, node)


def _deployment_targets(payload: dict[str, Any]) -> list[DeploymentTarget]:
    targets = payload.get("deployment_targets", [])
    if isinstance(targets, list) and targets:
        normalized: list[DeploymentTarget] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            url = str(target.get("public_url") or target.get("url") or "")
            if not url:
                continue
            service = str(target.get("service") or "")
            label = _target_label(service, url)
            normalized.append(DeploymentTarget(label=label, url=url, service=service))
        if normalized:
            return normalized

    urls = payload.get("public_urls", [])
    if isinstance(urls, list):
        return [
            DeploymentTarget(label=_target_label("", str(url)), url=str(url))
            for url in urls
            if str(url).startswith(("http://", "https://"))
        ]
    return []


def _target_label(service: str, url: str) -> str:
    service_label = service.strip().upper()
    if service_label:
        return service_label
    if "web" in url:
        return "WEB"
    if "api" in url:
        return "API"
    return "APP"


def _handoff_status(run_dir: Path, state: dict[str, Any]) -> str:
    status = str(state.get("status") or "")
    if status.startswith("handoff_"):
        return status.removeprefix("handoff_")
    sprint_id = _current_handoff_sprint_id(state)
    if _handoff_report_exists(run_dir, sprint_id=sprint_id):
        return "ready"
    return ""


def _stage_from_artifacts(run_dir: Path) -> str:
    if _handoff_report_exists(run_dir):
        return "handoff"
    project_management_dir = run_dir / "upstream-planning" / "project-management"
    if (project_management_dir / "release-plan.json").exists() or (
        project_management_dir / "candidate-feature-queue.json"
    ).exists():
        return "project_management"
    if (run_dir / "upstream-planning" / "architecture.json").exists():
        return "architecture"
    if (run_dir / "upstream-planning" / "business-analysis.json").exists():
        return "business_analysis"
    if (run_dir / "13-deployment-summary.md").exists():
        return "deployment"
    if list((run_dir / "qa").glob("results-*.json")):
        return "qa"
    if _execution_summary_paths(run_dir):
        return "fullstack"
    return "planning"


def _handoff_report_exists(run_dir: Path, *, sprint_id: str = "") -> bool:
    handoff_dir = run_dir / "handoff"
    if (handoff_dir / "project" / "final" / "release-report.html").exists():
        return True
    if (handoff_dir / "release-report.html").exists():
        return True
    if sprint_id:
        return (handoff_dir / "sprints" / sprint_id / "release-report.html").exists()
    return any(handoff_dir.glob("**/release-report.html"))


def _current_handoff_sprint_id(state: dict[str, Any]) -> str:
    sprint_id = str(state.get("team_lead_sprint_id") or "")
    if sprint_id:
        return sprint_id
    board = state.get("work_board", {})
    if isinstance(board, dict):
        return str(board.get("sprint_id") or "")
    return ""


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_optional_json_value(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _qa_status_from_artifact(path: Path) -> str:
    payload = _read_optional_json(path)
    return str(payload.get("status") or "")


def artifact_groups_for_run(run_dir: Path) -> list[ArtifactGroup]:
    """Build visible artifact groups from delivery state plus planning artifacts."""

    grouped: dict[str, list[ArtifactSpec]] = {}
    for filename, label, agent in UPSTREAM_PLANNING_ARTIFACTS:
        if (run_dir / filename).exists():
            grouped.setdefault(agent, []).append((filename, label, agent))

    for filename, label, agent in HEAD_ARTIFACTS:
        if (run_dir / filename).exists():
            grouped.setdefault(agent, []).append((filename, label, agent))

    for artifact in _delivery_state_artifacts(run_dir):
        path = str(artifact.get("path", ""))
        if not path or not (run_dir / path).exists():
            continue
        owner = str(artifact.get("owner_agent", ""))
        group = AGENT_ARTIFACT_LABELS.get(owner, _title_agent(owner))
        label = _artifact_label_from_path(path)
        agent = AGENT_ARTIFACT_LABELS.get(owner, owner or "Unknown Agent")
        grouped.setdefault(group, []).append((path, label, agent))

    groups: list[ArtifactGroup] = []
    ordered_names = [
        *[name for name in AGENT_ARTIFACT_ORDER if name in grouped],
        *sorted(name for name in grouped if name not in AGENT_ARTIFACT_ORDER),
    ]
    for name in ordered_names:
        artifacts = _dedupe_artifacts(grouped[name])
        groups.append((name, _agent_group_description(name), artifacts))
    return groups


def _delivery_state_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    state = _read_delivery_state(run_dir)
    artifacts = state.get("artifacts", [])
    return [artifact for artifact in artifacts if isinstance(artifact, dict)]


def _artifact_label_from_path(path: str) -> str:
    filename = Path(path).name
    feature = _feature_from_artifact_path(path)
    if filename == "prompt.md":
        base = "Codex prompt"
    elif filename == "execution.log":
        base = "Codex execution log"
    elif filename == "events.jsonl":
        base = "Codex raw events"
    elif filename == "summary.md":
        base = "Codex final summary"
    elif filename.startswith("07-execution-summary"):
        base = "Execution summary"
    elif filename.startswith("08-qa-report"):
        base = "QA report"
    elif filename.startswith("results-") and filename.endswith(".json"):
        base = "QA results"
    elif filename.startswith("10-fix-request"):
        base = "Fix request"
    elif filename.startswith("sprint-") and filename.endswith("-plan.json"):
        base = "Sprint plan"
    elif filename.startswith("sprint-") and filename.endswith("-history.json"):
        base = "Decision and tool history"
    elif filename.startswith("sprint-") and filename.endswith("-result.json"):
        base = "Sprint result"
    elif filename.endswith(".json") and "/decisions/" in path.replace("\\", "/"):
        base = (
            "Head decision" if path.replace("\\", "/").startswith("head/") else "Team Lead decision"
        )
    elif filename == "release-report.html":
        base = "Client release report"
    else:
        base = filename
    return f"{feature} - {base}" if feature else base


def _feature_from_artifact_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    for part in parts:
        if part.startswith("F") and part[1:].isdigit():
            return part
    filename = parts[-1]
    for token in filename.replace(".", "-").split("-"):
        if token.startswith("F") and token[1:].isdigit():
            return token
    return ""


def _dedupe_artifacts(artifacts: list[ArtifactSpec]) -> list[ArtifactSpec]:
    seen: set[str] = set()
    unique: list[ArtifactSpec] = []
    for artifact in artifacts:
        path = artifact[0]
        if path in seen:
            continue
        seen.add(path)
        unique.append(artifact)
    return unique


def _agent_group_description(name: str) -> str:
    descriptions = {
        "Business Analyst": (
            "Business requirements, users, acceptance criteria, scope, and open questions."
        ),
        "Head Agent": "Upstream planning coordination, decisions, and review artifacts.",
        "Architect": "Solution architecture, technical decisions, constraints, and diagrams.",
        "Project Manager": (
            "Release plans, sprint packages, feature sequencing, dependencies, and risks."
        ),
        "Fullstack Agent": (
            "Feature-scoped implementation summaries, prompts, logs, and generated files."
        ),
        "QA Agent": (
            "Feature-scoped QA reports, structured results, QA Codex attempts, and evidence."
        ),
        "Deployment Agent": "Deployment planning, execution, and post-deployment evidence.",
        "Team Lead Agent": (
            "Sprint coordination, AgentExecutor decisions, selected tools, and sprint outcome."
        ),
        "Documentation / Handoff Agent": (
            "Final user-facing handoff and delivery summary artifacts."
        ),
    }
    return descriptions.get(name, "Artifacts produced by this agent.")


def _title_agent(agent_id: str) -> str:
    if not agent_id:
        return "Unknown Agent"
    return agent_id.replace("-", " ").title()


def read_required_configuration(run_dir: Path) -> list[str]:
    intake_path = run_dir / "01-intake-brief.json"
    if not intake_path.exists():
        return []

    intake = read_json_artifact(intake_path)
    required = intake.get("required_configuration", [])
    if not isinstance(required, list):
        return []
    return [str(item) for item in required if str(item).strip()]


def default_env_value(key: str) -> str:
    return DEFAULT_ENV_VALUES.get(key, "")


def root_env_value(key: str, root: Path | None = None) -> str:
    return read_env_keys((root or repo_root()) / ".env").get(key, "").strip()


def initial_env_value(key: str, root: Path | None = None) -> str:
    return root_env_value(key, root) or default_env_value(key)


def missing_required_env_keys(run_dir: Path, values: dict[str, str] | None = None) -> list[str]:
    current = read_env_keys(_target_project_dir(run_dir) / ".env")
    proposed = values or {}
    missing: list[str] = []

    for key in read_required_configuration(run_dir):
        value = (
            proposed.get(key, "").strip()
            or current.get(key, "").strip()
            or default_env_value(key).strip()
        )
        if not value:
            missing.append(key)
    return missing


def ensure_required_env_defaults(run_dir: Path) -> Path:
    target_dir = _target_project_dir(run_dir)
    env_path = target_dir / ".env"
    current = read_env_keys(env_path)
    defaults = {
        key: default_env_value(key)
        for key in read_required_configuration(run_dir)
        if default_env_value(key) and not current.get(key, "").strip()
    }
    if defaults:
        return write_target_env(run_dir, defaults)
    return env_path


def write_target_env(run_dir: Path, values: dict[str, str]) -> Path:
    target_dir = _target_project_dir(run_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    env_path = target_dir / ".env"
    existing = read_env_keys(env_path)

    merged = {**existing}
    for key, value in values.items():
        if value.strip():
            merged[key] = value

    lines = [f"{key}={value}" for key, value in sorted(merged.items())]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOGGER.info("Wrote run-local env keys=%s run_dir=%s", sorted(merged), run_dir)
    return env_path


def read_env_keys(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value
    return values


def saved_env_keys(run_dir: Path) -> list[str]:
    return sorted(read_env_keys(_target_project_dir(run_dir) / ".env"))


def _target_project_dir(run_dir: Path) -> Path:
    request_path = run_dir / EXECUTION_REQUEST_ARTIFACT
    if request_path.exists():
        request = read_json_artifact(request_path)
        target = request.get("target_project_dir")
        if target:
            return Path(str(target))
    return run_dir / "generated-project"


def _codex_status_path(run_dir: Path) -> Path:
    return run_dir / ".codex-execution.status"


def _deployment_status_path(run_dir: Path) -> Path:
    return run_dir / ".azure-deployment.status"


def _read_codex_status(run_dir: Path) -> str:
    status_path = _codex_status_path(run_dir)
    if not status_path.exists():
        return ""
    return status_path.read_text(encoding="utf-8").strip()


def _read_deployment_status(run_dir: Path) -> str:
    status_path = _deployment_status_path(run_dir)
    if not status_path.exists():
        return ""
    return status_path.read_text(encoding="utf-8").strip()


def _run_codex_in_thread(run_dir: Path) -> None:
    status_path = _codex_status_path(run_dir)
    try:
        status_path.write_text("running\n", encoding="utf-8")
        LOGGER.info("Console execution graph thread running run_dir=%s", run_dir)
        _run_console_execution_graph(run_dir)
        status_path.write_text("completed\n", encoding="utf-8")
        LOGGER.info("Console execution graph thread completed run_dir=%s", run_dir)
    except Exception as exc:  # pragma: no cover - surfaced in local run artifacts
        status_path.write_text(f"failed\nerror={exc}\n", encoding="utf-8")
        LOGGER.exception("Console execution graph thread failed run_dir=%s", run_dir)


def _run_console_execution_graph(run_dir: Path) -> dict[str, Any]:
    runtime = DeliveryGraphRuntime(node_order=CONSOLE_EXECUTION_NODE_ORDER)
    return runtime.start(
        run_dir,
        run_id=run_dir.name,
        requirements_path=run_dir / "00-requirements.md",
        target_project_dir=_target_project_dir(run_dir),
    )


def _execution_summary_text(run_dir: Path) -> str:
    summaries = _execution_summary_paths(run_dir)
    if not summaries:
        return ""
    return "\n\n".join(f"# {path.name}\n\n{read_text_artifact(path)}" for path in summaries)


def _run_deployment_in_thread(run_dir: Path) -> None:
    status_path = _deployment_status_path(run_dir)
    try:
        status_path.write_text("running\n", encoding="utf-8")
        LOGGER.info("Console deployment graph thread running run_dir=%s", run_dir)
        DeliveryGraphRuntime(node_order=CONSOLE_DEPLOYMENT_NODE_ORDER).start(
            run_dir,
            run_id=run_dir.name,
            target_project_dir=_target_project_dir(run_dir),
        )
        status_path.write_text("completed\n", encoding="utf-8")
        LOGGER.info("Console deployment graph thread completed run_dir=%s", run_dir)
    except Exception as exc:  # pragma: no cover - surfaced in local run artifacts
        status_path.write_text(f"failed\nerror={exc}\n", encoding="utf-8")
        LOGGER.exception("Console deployment graph thread failed run_dir=%s", run_dir)


def _run_id(prefix: str = "console") -> str:
    safe_prefix = prefix.strip().strip("-") or "console"
    return safe_prefix + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
