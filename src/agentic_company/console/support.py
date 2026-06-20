"""Process and environment helpers for the FastAPI product console."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic_company.orchestration import DeliveryGraphRuntime
from agentic_company.orchestration.graphs import (
    CONSOLE_DEPLOYMENT_NODE_ORDER,
    CONSOLE_EXECUTION_NODE_ORDER,
)
from agentic_company.platform.artifacts.artifacts import read_json_artifact

LOGGER = logging.getLogger(__name__)

_CODEX_THREADS: dict[str, threading.Thread] = {}
_DEPLOYMENT_THREADS: dict[str, threading.Thread] = {}

DEFAULT_ENV_VALUES = {
    "AGENT_LLM_MODEL": "gpt-4.1",
    "COORDINATOR_AGENT_REASONING_EFFORT": "none",
    "SPECIALIST_AGENT_REASONING_EFFORT": "none",
}
DEFAULT_SAMPLE_REQUIREMENTS = "multi-service-task-tracker.md"
AGENT_RUNTIME_ENV_RELATIVE_PATH = Path("delivery") / "agent-runtime.env"
CODEX_PROCESS = "codex_execution"
DEPLOYMENT_PROCESS = "azure_deployment"
AGENT_RUNTIME_ENV_PROCESS = "agent_runtime_env"
PROCESS_HEARTBEAT_SECONDS = 30
SECRET_RUNTIME_ENV_KEYS = {
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CODEX_ACCESS_TOKEN",
    "CODEX_REFRESH_TOKEN",
}


@dataclass(slots=True)
class CleanupResult:
    deleted: int
    skipped: list[str]


@dataclass(slots=True)
class FeatureProgress:
    work_item_id: str
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
    work_item_id: str
    reason: str
    status: str


@dataclass(slots=True)
class CurrentWork:
    stage: str
    work_item_id: str
    title: str
    status: str
    lane: str
    owner: str
    assigned_agent: str
    last_tool: str
    last_work_item_id: str
    last_reason: str
    last_status: str


@dataclass(slots=True)
class DeliveryOverview:
    run_id: str
    stage: str
    status: str
    active_work_item_id: str | None
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
        return sum(1 for feature in self.features if feature.status in {"done", "qa_passed"})

    @property
    def total_feature_count(self) -> int:
        return len(self.features)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def list_sample_requirements(root: Path | None = None) -> list[Path]:
    samples_dir = (root or repo_root()) / "examples" / "requirements"
    return sorted(samples_dir.glob("*.md")) if samples_dir.exists() else []


def sample_requirements_path(
    root: Path | None = None,
    filename: str = DEFAULT_SAMPLE_REQUIREMENTS,
) -> Path:
    return (root or repo_root()) / "examples" / "requirements" / filename


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
    (output_dir / "00-requirements.md").write_text(
        requirements_text.strip() + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Created console run shell run_id=%s output_dir=%s", run_id, output_dir)
    return output_dir


def clear_console_runs(output_root: Path | None = None) -> CleanupResult:
    root = output_root or repo_root() / "runs"
    if not root.exists():
        return CleanupResult(deleted=0, skipped=[])
    deleted = 0
    skipped: list[str] = []
    for path in root.iterdir():
        if not path.is_dir() or not path.name.startswith("console-"):
            continue
        try:
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()
            deleted += 1
        except OSError:
            skipped.append(path.name)
    return CleanupResult(deleted=deleted, skipped=skipped)


def start_codex_execution(run_dir: Path) -> int:
    if _thread_running(_CODEX_THREADS.get(str(run_dir))) or _process_is_terminal(
        run_dir, CODEX_PROCESS
    ):
        LOGGER.info("Codex execution start skipped run_dir=%s", run_dir)
        return 0
    status_path = _codex_status_path(run_dir)
    status_path.write_text("starting\n", encoding="utf-8")
    _record_process_state(run_dir, CODEX_PROCESS, "starting")
    thread = threading.Thread(
        target=_run_codex_in_thread,
        args=(run_dir,),
        name=f"codex-execution-{run_dir.name}",
        daemon=True,
    )
    _CODEX_THREADS[str(run_dir)] = thread
    thread.start()
    status_path.write_text(f"running\nthread={thread.name}\n", encoding="utf-8")
    _record_process_state(run_dir, CODEX_PROCESS, "running", thread_name=thread.name)
    LOGGER.info("Started Codex execution thread=%s run_dir=%s", thread.name, run_dir)
    return thread.ident or 0


def start_azure_deployment(run_dir: Path) -> int:
    if _thread_running(_DEPLOYMENT_THREADS.get(str(run_dir))) or _process_is_terminal(
        run_dir, DEPLOYMENT_PROCESS
    ):
        LOGGER.info("Deployment start skipped run_dir=%s", run_dir)
        return 0
    status_path = _deployment_status_path(run_dir)
    status_path.write_text("starting\n", encoding="utf-8")
    _record_process_state(run_dir, DEPLOYMENT_PROCESS, "starting")
    thread = threading.Thread(
        target=_run_deployment_in_thread,
        args=(run_dir,),
        name=f"azure-deployment-{run_dir.name}",
        daemon=True,
    )
    _DEPLOYMENT_THREADS[str(run_dir)] = thread
    thread.start()
    status_path.write_text(f"running\nthread={thread.name}\n", encoding="utf-8")
    _record_process_state(run_dir, DEPLOYMENT_PROCESS, "running", thread_name=thread.name)
    LOGGER.info("Started deployment thread=%s run_dir=%s", thread.name, run_dir)
    return thread.ident or 0


def request_codex_execution_stop(run_dir: Path) -> Path:
    path = run_dir / ".codex-execution.stop"
    path.write_text("stop\n", encoding="utf-8")
    (run_dir / ".stop-requested").write_text("stop\n", encoding="utf-8")
    _request_process_stop(run_dir, CODEX_PROCESS)
    return path


def agent_runtime_env_path(run_dir: Path) -> Path:
    return run_dir / AGENT_RUNTIME_ENV_RELATIVE_PATH


def write_target_env(run_dir: Path, values: dict[str, str]) -> Path:
    env_path = agent_runtime_env_path(run_dir)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_env_keys(env_path)
    merged = {**existing}
    for key, value in values.items():
        if _is_secret_runtime_env_key(key):
            LOGGER.warning("Refusing to persist secret runtime env key=%s run_dir=%s", key, run_dir)
            continue
        if str(value).strip():
            merged[key] = str(value)
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(merged.items())) + "\n",
        encoding="utf-8",
    )
    _record_process_state(
        run_dir,
        AGENT_RUNTIME_ENV_PROCESS,
        "written",
        env_keys=sorted(merged),
    )
    LOGGER.info("Wrote agent runtime env keys=%s run_dir=%s", sorted(merged), run_dir)
    return env_path


def _is_secret_runtime_env_key(key: str) -> bool:
    upper = key.strip().upper()
    if upper in SECRET_RUNTIME_ENV_KEYS:
        return True
    return upper.endswith("_TOKEN") or upper.endswith("_SECRET") or upper.endswith("_PASSWORD")


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
    return sorted(read_env_keys(agent_runtime_env_path(run_dir)))


def default_env_value(key: str) -> str:
    return DEFAULT_ENV_VALUES.get(key, "")


def root_env_value(key: str, root: Path | None = None) -> str:
    return read_env_keys((root or repo_root()) / ".env").get(key, "").strip()


def initial_env_value(key: str, root: Path | None = None) -> str:
    return root_env_value(key, root) or default_env_value(key)


def read_required_configuration(run_dir: Path) -> list[str]:
    intake_path = run_dir / "01-intake-brief.json"
    if not intake_path.exists():
        return []
    intake = read_json_artifact(intake_path)
    required = intake.get("required_configuration", [])
    return [str(item) for item in required] if isinstance(required, list) else []


def missing_required_env_keys(run_dir: Path, values: dict[str, str] | None = None) -> list[str]:
    current = read_env_keys(agent_runtime_env_path(run_dir))
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


def _run_codex_in_thread(run_dir: Path) -> None:
    status_path = _codex_status_path(run_dir)
    stop_heartbeat, heartbeat_thread = _start_process_heartbeat(run_dir, CODEX_PROCESS)
    try:
        status_path.write_text("running\n", encoding="utf-8")
        _record_process_state(
            run_dir,
            CODEX_PROCESS,
            "running",
            thread_name=threading.current_thread().name,
        )
        LOGGER.info("Console execution graph thread running run_dir=%s", run_dir)
        _run_console_execution_graph(run_dir)
        status_path.write_text("completed\n", encoding="utf-8")
        _record_process_state(run_dir, CODEX_PROCESS, "completed")
        LOGGER.info("Console execution graph thread completed run_dir=%s", run_dir)
    except Exception as exc:  # pragma: no cover - surfaced in local run artifacts
        status_path.write_text(f"failed\nerror={exc}\n", encoding="utf-8")
        _record_process_state(run_dir, CODEX_PROCESS, "failed", error=str(exc))
        LOGGER.exception("Console execution graph thread failed run_dir=%s", run_dir)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1)


def _run_deployment_in_thread(run_dir: Path) -> None:
    status_path = _deployment_status_path(run_dir)
    stop_heartbeat, heartbeat_thread = _start_process_heartbeat(
        run_dir,
        DEPLOYMENT_PROCESS,
    )
    try:
        status_path.write_text("running\n", encoding="utf-8")
        _record_process_state(
            run_dir,
            DEPLOYMENT_PROCESS,
            "running",
            thread_name=threading.current_thread().name,
        )
        LOGGER.info("Console deployment graph thread running run_dir=%s", run_dir)
        DeliveryGraphRuntime(node_order=CONSOLE_DEPLOYMENT_NODE_ORDER).start(
            run_dir,
            run_id=run_dir.name,
            target_project_dir=_target_project_dir(run_dir),
        )
        status_path.write_text("completed\n", encoding="utf-8")
        _record_process_state(run_dir, DEPLOYMENT_PROCESS, "completed")
        LOGGER.info("Console deployment graph thread completed run_dir=%s", run_dir)
    except Exception as exc:  # pragma: no cover - surfaced in local run artifacts
        status_path.write_text(f"failed\nerror={exc}\n", encoding="utf-8")
        _record_process_state(run_dir, DEPLOYMENT_PROCESS, "failed", error=str(exc))
        LOGGER.exception("Console deployment graph thread failed run_dir=%s", run_dir)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1)


def _run_console_execution_graph(run_dir: Path) -> dict[str, Any]:
    runtime = DeliveryGraphRuntime(node_order=CONSOLE_EXECUTION_NODE_ORDER)
    return runtime.start(
        run_dir,
        run_id=run_dir.name,
        requirements_path=run_dir / "00-requirements.md",
        target_project_dir=_target_project_dir(run_dir),
    )


def _target_project_dir(run_dir: Path) -> Path:
    from agentic_company.console.web.db import ConsoleRepository

    run = ConsoleRepository().get_run_by_uid(run_dir.name)
    if run is None or not run.target_project_dir.strip():
        raise ValueError(f"Missing DB target_project_dir contract for run {run_dir.name}")
    return Path(run.target_project_dir)


def _codex_status_path(run_dir: Path) -> Path:
    return run_dir / ".codex-execution.status"


def _deployment_status_path(run_dir: Path) -> Path:
    return run_dir / ".azure-deployment.status"


def _thread_running(thread: threading.Thread | None) -> bool:
    return bool(thread and thread.is_alive())


def _process_is_terminal(run_dir: Path, process_name: str) -> bool:
    state = _process_state(run_dir, process_name)
    return bool(state and state.status.lower() in {"completed", "failed"})


def _start_process_heartbeat(
    run_dir: Path,
    process_name: str,
) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()
    owner_thread_name = threading.current_thread().name

    def heartbeat() -> None:
        while not stop_event.wait(PROCESS_HEARTBEAT_SECONDS):
            state = _process_state(run_dir, process_name)
            if state and state.status.lower() in {"completed", "failed"}:
                break
            if state and state.status.lower() == "stop_requested":
                continue
            _record_process_state(
                run_dir,
                process_name,
                "running",
                thread_name=owner_thread_name,
            )

    thread = threading.Thread(
        target=heartbeat,
        name=f"{process_name}-heartbeat-{run_dir.name}",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _record_process_state(
    run_dir: Path,
    process_name: str,
    status: str,
    *,
    thread_name: str = "",
    env_keys: list[str] | None = None,
    error: str = "",
) -> None:
    run_id = _db_run_id_for_run_dir(run_dir)
    if run_id is None:
        return
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    repo.upsert_console_process_state(
        run_id,
        process_name=process_name,
        status=status,
        thread_name=thread_name,
        env_keys=env_keys,
        error=error,
    )


def _request_process_stop(run_dir: Path, process_name: str) -> None:
    run_id = _db_run_id_for_run_dir(run_dir)
    if run_id is None:
        return
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    repo.request_console_process_stop(run_id, process_name=process_name)


def _process_state(run_dir: Path, process_name: str):
    run_id = _db_run_id_for_run_dir(run_dir)
    if run_id is None:
        return None
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    return repo.get_console_process_state(run_id, process_name)


def _db_run_id_for_run_dir(run_dir: Path) -> int | None:
    from agentic_company.console.web.db import ConsoleRepository

    repo = ConsoleRepository()
    repo.init_schema()
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT id FROM runs WHERE run_dir = ? OR run_uid = ?",
            (str(run_dir), run_dir.name),
        ).fetchone()
    return int(row["id"]) if row else None


def _run_id(prefix: str = "console") -> str:
    safe_prefix = prefix.strip().strip("-") or "console"
    return safe_prefix + "-" + datetime.now().strftime("%Y%m%d-%H%M%S")
