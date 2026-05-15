"""Support helpers for the local planning console."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_company.agents.planning import run_pipeline
from agentic_company.orchestration import DeliveryGraphRuntime
from agentic_company.orchestration.graphs import (
    CONSOLE_DEPLOYMENT_NODE_ORDER,
    CONSOLE_EXECUTION_NODE_ORDER,
)

LOGGER = logging.getLogger(__name__)

_CODEX_THREADS: dict[str, threading.Thread] = {}
_DEPLOYMENT_THREADS: dict[str, threading.Thread] = {}
DEFAULT_ENV_VALUES = {
    "DEFAULT_MODEL": "gpt-4o-mini",
}
DEFAULT_SAMPLE_REQUIREMENTS = "web-app-mvp-chat.md"


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


@dataclass(slots=True)
class DeploymentTarget:
    label: str
    url: str
    service: str = ""


@dataclass(slots=True)
class DeliveryOverview:
    run_id: str
    stage: str
    status: str
    project_archetype: str
    active_feature_id: str | None
    features: list[FeatureProgress]
    qa_status: str
    deployment_status: str
    handoff_status: str
    topology_summary: str
    deployment_targets: list[DeploymentTarget]
    blockers: list[str]

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


ArtifactSpec = tuple[str, str, str]
ArtifactGroup = tuple[str, str, list[ArtifactSpec]]

PLANNING_ARTIFACTS = [
    ("01-intake-brief.json", "Intake brief", "Intake Agent"),
    ("02-project-classification.json", "Classification", "Project Classifier"),
    ("03-staffing-decision.json", "Staffing decision", "Team Assembler Agent"),
    ("04-workflow-plan.json", "Workflow plan", "Workflow Planner"),
    ("05-implementation-brief.md", "Implementation brief", "Tech Lead Agent"),
    ("06-execution-request.json", "Execution request", "Fullstack Agent"),
]

EXECUTION_ARTIFACTS = [
    ("07-execution-summary.md", "Execution summary", "Fullstack Agent"),
    ("08-qa-report.md", "QA report", "QA Agent"),
]

DEPLOYMENT_ARTIFACTS = [
    ("13-deployment-summary.md", "Deployment summary", "Deployment Agent"),
]

HANDOFF_ARTIFACTS = [
    ("09-handoff-summary.md", "Handoff summary", "Documentation / Handoff Agent"),
    ("handoff/release-report.html", "Release report", "Documentation / Handoff Agent"),
    ("handoff/release-evidence.json", "Release evidence", "Documentation / Handoff Agent"),
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

ARTIFACTS = PLANNING_ARTIFACTS + EXECUTION_ARTIFACTS + DEPLOYMENT_ARTIFACTS + HANDOFF_ARTIFACTS

ARTIFACT_GROUPS: list[ArtifactGroup] = [
    (
        "Planning",
        "Business intake, classification, staffing, workflow, and implementation brief.",
        PLANNING_ARTIFACTS,
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
    "pipeline": "L0 Deterministic",
    "intake-agent": "L0 Deterministic",
    "project-classifier": "L0 Deterministic",
    "team-assembler-agent": "L0 Deterministic",
    "workflow-planner": "L0 Deterministic",
    "tech-lead-agent": "L0 Deterministic",
    "fullstack-agent": "L6 Codex Agent",
    "qa-agent": "L6 Codex QA Agent",
    "qa-codex-agent": "L6 Codex QA Agent",
    "deployment-codex-agent": "L6 Codex Deployment Agent",
    "documentation-handoff-agent": "L6 Codex Handoff Agent",
    "handoff-codex-agent": "L6 Codex Handoff Agent",
    "deployment-agent": "L6 Codex Deployment Agent",
}

AGENT_ARTIFACT_LABELS = {
    "planning-agent": "Planning Agent",
    "intake-agent": "Planning Agent",
    "project-classifier": "Planning Agent",
    "team-assembler-agent": "Planning Agent",
    "workflow-planner": "Planning Agent",
    "tech-lead-agent": "Planning Agent",
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
    "Planning Agent",
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


def create_console_run(requirements_text: str, output_root: Path | None = None) -> Path:
    output_base = output_root or repo_root() / "runs"
    run_id = _run_id()
    output_dir = output_base / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    requirements_path = output_dir / "00-requirements.md"
    requirements_path.write_text(requirements_text.strip() + "\n", encoding="utf-8")
    LOGGER.info("Created console run shell run_id=%s output_dir=%s", run_id, output_dir)
    return run_pipeline(requirements_path, output_base, run_id=run_id)


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
    thread = _CODEX_THREADS.get(str(run_dir))
    if thread and thread.is_alive():
        return True
    status_text = _read_codex_status(run_dir)
    if status_text.startswith(("failed", "completed", "stopped")):
        return False
    if status_text.startswith(("starting", "running")):
        state = _read_delivery_state(run_dir)
        return not (_delivery_execution_terminal(state) and review_completed(run_dir))
    if execution_completed(run_dir):
        return False
    if (run_dir / "codex" / "execution.log").exists() and not (
        run_dir / "07-execution-summary.md"
    ).exists():
        return True
    if _feature_codex_log_paths(run_dir):
        return True
    return False


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
    summary_path = run_dir / "13-deployment-summary.md"
    if not summary_path.exists():
        return False
    return "Status: deployed" in summary_path.read_text(encoding="utf-8")


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
    return sorted(codex_dir.glob("*/execution.log"))


def _read_delivery_state(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir / ".delivery-state.json"
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def execution_completed(run_dir: Path) -> bool:
    state = _read_delivery_state(run_dir)
    if _delivery_execution_terminal(state):
        return True
    summaries = _execution_summary_paths(run_dir)
    if not summaries:
        return False
    return all("Status: codex failed" not in path.read_text(encoding="utf-8") for path in summaries)


def _delivery_execution_terminal(state: dict[str, Any]) -> bool:
    status = str(state.get("status", ""))
    return status in {
        "fullstack_feature_queue_completed_downstream_paused",
        "feature_queue_qa_completed_downstream_paused",
    } or status.startswith("deployment_")


def _summary_has_failed(run_dir: Path) -> bool:
    return any(
        "Status: codex failed" in path.read_text(encoding="utf-8")
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
        event["runtime"] = RUNTIME_BY_AGENT.get(agent_id, "L0 Deterministic")
        events.append(event)
    return events


def read_json_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    feature_queue = _feature_queue_from_state_or_plan(run_dir, state)
    feature_statuses = _as_dict(state.get("feature_statuses", {}))
    completed_feature_ids = {
        str(feature_id) for feature_id in state.get("completed_feature_ids", []) if feature_id
    }
    active_feature_id = _optional_str(state.get("active_feature_id"))
    repair_attempts = _as_dict(state.get("feature_repair_attempts", {}))

    features = [
        _feature_progress(
            feature,
            feature_statuses=feature_statuses,
            completed_feature_ids=completed_feature_ids,
            active_feature_id=active_feature_id,
            repair_attempts=repair_attempts,
        )
        for feature in feature_queue
    ]

    return DeliveryOverview(
        run_id=str(state.get("run_id") or run_dir.name),
        stage=str(state.get("stage") or _stage_from_artifacts(run_dir)),
        status=str(state.get("status") or "planning_ready"),
        project_archetype=str(state.get("project_archetype") or _project_archetype(run_dir)),
        active_feature_id=active_feature_id,
        features=features,
        qa_status=str(state.get("qa_status") or _feature_queue_qa_status(features, run_dir)),
        deployment_status=str(
            state.get("deployment_status") or deployment_result.get("status") or ""
        ),
        handoff_status=_handoff_status(run_dir, state),
        topology_summary=str(deployment_result.get("topology_summary") or ""),
        deployment_targets=_deployment_targets(deployment_result),
        blockers=[str(blocker) for blocker in state.get("blockers", []) if blocker],
    )


def _feature_queue_from_state_or_plan(
    run_dir: Path,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    state_queue = state.get("feature_queue", [])
    if isinstance(state_queue, list) and state_queue:
        return [item for item in state_queue if isinstance(item, dict)]

    workflow = _read_optional_json(run_dir / "04-workflow-plan.json")
    workflow_queue = workflow.get("feature_queue", [])
    if isinstance(workflow_queue, list):
        return [item for item in workflow_queue if isinstance(item, dict)]
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
    )


def _feature_queue_qa_status(features: list[FeatureProgress], run_dir: Path) -> str:
    if not features:
        return _qa_status_from_artifact(run_dir / "qa" / "results.json")
    if any(feature.status.startswith(("qa_failed", "blocked", "failed")) for feature in features):
        return "failed"
    if all(feature.status in _FEATURE_DONE_STATUSES for feature in features):
        return "passed"
    return ""


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
    if (run_dir / "handoff" / "release-report.html").exists():
        return "ready"
    return ""


def _stage_from_artifacts(run_dir: Path) -> str:
    if (run_dir / "handoff" / "release-report.html").exists():
        return "handoff"
    if (run_dir / "13-deployment-summary.md").exists():
        return "deployment"
    if list((run_dir / "qa").glob("results-*.json")):
        return "qa"
    if _execution_summary_paths(run_dir):
        return "fullstack"
    return "planning"


def _project_archetype(run_dir: Path) -> str:
    request = _read_optional_json(run_dir / "06-execution-request.json")
    return str(request.get("project_archetype") or "")


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


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
    for filename, label, agent in PLANNING_ARTIFACTS:
        if (run_dir / filename).exists():
            grouped.setdefault("Planning Agent", []).append((filename, label, agent))

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
    elif filename == "release-report.html":
        base = "Client release report"
    elif filename == "release-evidence.json":
        base = "Release evidence"
    elif filename == "09-handoff-summary.md":
        base = "Handoff summary"
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
        "Planning Agent": (
            "Planning intake, classification, staffing, workflow, and handoff requests."
        ),
        "Fullstack Agent": (
            "Feature-scoped implementation summaries, prompts, logs, and generated files."
        ),
        "QA Agent": (
            "Feature-scoped QA reports, structured results, QA Codex attempts, and evidence."
        ),
        "Deployment Agent": "Deployment planning, execution, and post-deployment evidence.",
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
    request_path = run_dir / "06-execution-request.json"
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
        target_project_dir=_target_project_dir(run_dir),
    )


def _execution_summary_text(run_dir: Path) -> str:
    summaries = _execution_summary_paths(run_dir)
    if not summaries:
        return ""
    return "\n\n".join(f"# {path.name}\n\n{path.read_text(encoding='utf-8')}" for path in summaries)


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


def _run_id() -> str:
    return "console-" + datetime.now().strftime("%Y%m%d-%H%M%S")
