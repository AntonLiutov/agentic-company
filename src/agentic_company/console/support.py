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


@dataclass(slots=True)
class CleanupResult:
    deleted: int
    skipped: list[str]


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
]

DEPLOYMENT_DETAIL_ARTIFACTS = [
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
        "Final Azure deployment result.",
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
        "Prepared Azure plan/request files used by the deployment runner.",
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
    "qa-agent": "L2 Tool Executor",
    "documentation-handoff-agent": "L0 Deterministic",
    "deployment-agent": "L2 Tool Executor",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def sample_requirements_path(root: Path | None = None) -> Path:
    base = root or repo_root()
    return base / "examples" / "requirements" / "web-app-mvp-chat.md"


def load_sample_requirements(root: Path | None = None) -> str:
    return sample_requirements_path(root).read_text(encoding="utf-8")


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
        LOGGER.info("Azure deployment start skipped run_dir=%s", run_dir)
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
    LOGGER.info("Started Azure deployment thread=%s run_dir=%s", thread.name, run_dir)
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
        return not (execution_completed(run_dir) and review_completed(run_dir))
    if execution_completed(run_dir):
        return False
    if (run_dir / "codex" / "execution.log").exists() and not (
        run_dir / "07-execution-summary.md"
    ).exists():
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
    return (run_dir / "qa" / "results.json").exists()


def execution_completed(run_dir: Path) -> bool:
    summary_path = run_dir / "07-execution-summary.md"
    if not summary_path.exists():
        return False
    summary = summary_path.read_text(encoding="utf-8")
    return "Status: codex failed" not in summary


def _summary_has_failed(run_dir: Path) -> bool:
    summary_path = run_dir / "07-execution-summary.md"
    if not summary_path.exists():
        return False
    return "Status: codex failed" in summary_path.read_text(encoding="utf-8")


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
    summary_path = run_dir / "07-execution-summary.md"
    if not summary_path.exists():
        return ""
    return summary_path.read_text(encoding="utf-8")


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
