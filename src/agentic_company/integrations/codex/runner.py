"""Codex CLI process execution helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from agentic_company.integrations.codex.cli import (
    AGENTIC_CODEX_BINARY_MODE_ENV,
    resolve_codex_binary,
)
from agentic_company.integrations.codex.events import append_raw_codex_event
from agentic_company.integrations.commands import StreamedCommand, stream_command
from agentic_company.platform.run.run_trace import record_raw_log_event, record_run_event

CODEX_SANDBOX_ENV = "AGENTIC_CODEX_SANDBOX"
CODEX_INHERIT_ENV_ENV = "AGENTIC_CODEX_INHERIT_ENV"
CODEX_UV_CACHE_DIR_ENV = "AGENTIC_CODEX_UV_CACHE_DIR"
CODEX_DENO_DIR_ENV = "AGENTIC_CODEX_DENO_DIR"
CODEX_NPM_CACHE_ENV = "AGENTIC_CODEX_NPM_CACHE"
CODEX_REASONING_EFFORT_ENV = "AGENTIC_CODEX_REASONING_EFFORT"
CODEX_SERVICE_TIER_ENV = "AGENTIC_CODEX_SERVICE_TIER"
CODEX_API_KEY_ENV = "CODEX_API_KEY"
CODEX_ENV_PASSTHROUGH_ENV = "AGENTIC_CODEX_ENV_PASSTHROUGH"
CODEX_WORKSPACE_NETWORK_ENV = "AGENTIC_CODEX_WORKSPACE_NETWORK"

# Host environment variables inherited by Codex specialist subprocesses. Anything
# outside this allowlist is dropped so unrelated host secrets never reach the
# Codex worker. Names are matched case-insensitively; the prefixes cover the
# platform's own CODEX_*/AGENTIC_* configuration plus the cloud/runtime tooling
# the workers shell out to. Additional names can be allowed at runtime via
# AGENTIC_CODEX_ENV_PASSTHROUGH (comma-separated) without a code change.
_CODEX_ENV_ALLOWED_NAMES = frozenset(
    name.upper()
    for name in (
        # POSIX essentials.
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "TMPDIR",
        "TZ",
        # Windows essentials.
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "USERPROFILE",
        "USERNAME",
        "USERDOMAIN",
        "HOMEDRIVE",
        "HOMEPATH",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        # Cloud / container tooling the workers invoke.
        "AZURE_CONFIG_DIR",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DOCKER_CLI_PLUGIN_EXTRA_DIRS",
        "KUBECONFIG",
        # Network proxy configuration (infrastructure settings, not secrets).
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "FTP_PROXY",
        # Platform runtime cache root the worker writes its caches under.
        "AGENTIC_RUNTIME_CACHE_DIR",
    )
)
_CODEX_ENV_ALLOWED_PREFIXES = (
    "CODEX_",
    # Only the worker-facing AGENTIC_CODEX_* config, not platform internals such
    # as AGENTIC_DATABASE_URL, which the Codex worker has no business reading.
    "AGENTIC_CODEX_",
    "OPENAI_",
    "AZURE_",
    "DOCKER_",
    "UV_",
    "NODE_",
    "NPM_",
    "DENO_",
    # QA browser runtime (Playwright pre-installed Chromium location/config).
    "PLAYWRIGHT_",
    "LC_",
)
DEFAULT_CODEX_MODEL = "gpt-5.5"
# Least-privilege default: workers write only inside the workspace. Agents that
# genuinely need host/network access (deployment, fullstack) opt into a broader
# sandbox explicitly.
DEFAULT_CODEX_SANDBOX = "workspace-write"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
DEFAULT_CODEX_SERVICE_TIER = "standard"
VALID_CODEX_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
VALID_CODEX_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
VALID_CODEX_SERVICE_TIERS = {"fast", "standard"}


def build_codex_exec_command(
    *,
    codex_binary: str | None,
    model: str,
    sandbox: str,
    target_project_dir: str,
    run_dir: Path,
    summary_path: Path,
    force_sandbox: bool = False,
    resume_session_id: str = "",
    include_host_tool_dirs: bool = False,
) -> list[str]:
    """Build the low-level `codex exec` command.

    Host cloud-auth directories (``~/.azure``, ``~/.docker``) are mounted only
    when ``include_host_tool_dirs`` is set, so a worker that does not deploy never
    gains read access to host cloud credentials.
    """

    binary = codex_binary or resolve_codex_binary()
    effective_sandbox = (
        sandbox or DEFAULT_CODEX_SANDBOX
        if force_sandbox
        else codex_sandbox_from_env(sandbox or DEFAULT_CODEX_SANDBOX)
    )
    config_args = list(codex_exec_config_args_from_env())
    # workspace-write blocks outbound network by default; re-enable it so QA can
    # verify deployed public URLs and agents can fetch network resources.
    if effective_sandbox == "workspace-write" and _workspace_network_enabled():
        config_args.extend(["--config", "sandbox_workspace_write.network_access=true"])
    command = [
        binary,
        "exec",
        *config_args,
        "--model",
        model,
        "--sandbox",
        effective_sandbox,
        "--cd",
        target_project_dir,
        "--add-dir",
        str(run_dir),
    ]
    if include_host_tool_dirs:
        for extra_dir in _codex_host_tool_dirs():
            command.extend(["--add-dir", extra_dir])
    command.extend(
        [
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            str(summary_path),
        ]
    )
    if resume_session_id:
        command.extend(["resume", resume_session_id, "-"])
    else:
        command.append("-")
    return command


_NATIVE_SKILLS_READY: set[str] = set()


def _ensure_native_skills(command: Sequence[str]) -> None:
    """Provision the skill catalog into Codex's NATIVE ``.agents/skills`` discovery path.

    This replaces the old hand-injected skill index: instead of pasting a skill list
    into every prompt, we drop the catalog where Codex itself auto-discovers it and
    triggers each skill by its ``description`` (progressive disclosure) — exactly as the
    Codex skills docs prescribe. The worker runs with cwd = ``<run>/generated-project``,
    and Codex scans ``$CWD/../.agents/skills``; we provision into that cwd-parent so the
    skills sit outside the deliverable working tree (never leak into the project PR).
    Done once per run workspace. Guarded: never breaks an exec.
    """

    try:
        target = _target_project_dir_from_command(command)
        if target is None:
            return
        workspace = Path(target).parent  # Codex's $CWD/../.agents/skills scan root
        key = str(workspace)
        if key in _NATIVE_SKILLS_READY:
            return
        from agentic_company.platform.skills import provision_native_skills

        provision_native_skills(workspace)
        _NATIVE_SKILLS_READY.add(key)
    except Exception:  # skill provisioning must never break a Codex run
        return


def stream_codex_exec_to_log(
    command: Sequence[str],
    prompt: str,
    timeout_seconds: int,
    log_path: Path,
    raw_events_path: Path,
    env: dict[str, str] | None = None,
    codex_execution_id: str = "",
    trace_run_dir: Path | None = None,
    trace_run_id: int | str = "",
    trace_agent_id: str = "",
    trace_work_item_id: str | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Codex while streaming command output and raw JSON events to artifacts."""

    _ensure_native_skills(command)
    raw_events_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    raw_events_path.write_text("", encoding="utf-8")
    log_path.write_text(f"timeout_seconds={timeout_seconds}\n\n", encoding="utf-8")
    command_env = env or build_codex_exec_environment(_target_project_dir_from_command(command))
    lock_path = _codex_execution_lock_path(
        trace_run_dir=trace_run_dir,
        default_dir=raw_events_path.parent,
        codex_execution_id=codex_execution_id,
    )
    lock_handle: int | None = None
    if lock_path is not None:
        lock_handle = _try_acquire_codex_execution_lock(lock_path)
        if lock_handle is None:
            _record_duplicate_execution_suppressed(
                trace_run_dir=trace_run_dir,
                trace_run_id=trace_run_id,
                trace_agent_id=trace_agent_id,
                trace_work_item_id=trace_work_item_id,
                codex_execution_id=codex_execution_id,
            )
            if _wait_for_codex_execution_unlock(lock_path, timeout_seconds):
                return subprocess.CompletedProcess(
                    args=list(command),
                    returncode=0,
                    stdout="",
                    stderr="duplicate_execution_suppressed",
                )
            return subprocess.CompletedProcess(
                args=list(command),
                returncode=124,
                stdout="",
                stderr="duplicate_execution_lock_timeout",
            )

    raw_log_seq = 0

    def on_stdout_line(line: str) -> None:
        nonlocal raw_log_seq
        raw_log_seq += 1
        event = append_raw_codex_event(
            raw_events_path,
            line,
            metadata={
                "codex_execution_id": codex_execution_id,
                "agent_id": trace_agent_id,
                "work_item_id": trace_work_item_id or "",
            },
        )
        _record_raw_codex_log_line(
            event if event is not None else line,
            seq=raw_log_seq,
            trace_run_id=trace_run_id,
            trace_agent_id=trace_agent_id,
            trace_work_item_id=trace_work_item_id,
            codex_execution_id=codex_execution_id,
        )
        if event:
            _record_codex_progress_event(
                event,
                trace_run_dir=trace_run_dir,
                trace_run_id=trace_run_id,
                trace_agent_id=trace_agent_id,
                trace_work_item_id=trace_work_item_id,
            )

    effective_stop_requested = stop_requested
    if effective_stop_requested is None and trace_run_id:
        stop_run_dir = trace_run_dir or raw_events_path.parent

        def effective_stop_requested() -> bool:
            try:
                from agentic_company.platform.db.runtime_db import run_stop_requested

                return run_stop_requested(str(trace_run_id), stop_run_dir)
            except Exception:
                return False

    try:
        return stream_command(
            StreamedCommand(
                command=command,
                cwd=log_path.parent,
                timeout_seconds=timeout_seconds,
                log_path=log_path,
                input_text=prompt,
                env=command_env,
                on_stdout_line=on_stdout_line,
                terminal_output_predicate=_is_codex_terminal_event,
                stop_requested=effective_stop_requested,
            )
        )
    finally:
        if lock_handle is not None and lock_path is not None:
            os.close(lock_handle)
            try:
                lock_path.unlink()
            except OSError:
                pass


def _record_raw_codex_log_line(
    payload: dict[str, object] | str,
    *,
    seq: int,
    trace_run_id: int | str,
    trace_agent_id: str,
    trace_work_item_id: str | None,
    codex_execution_id: str,
) -> None:
    if not trace_run_id or not trace_agent_id:
        return
    if isinstance(payload, dict):
        message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        created_at = str(payload.get("recorded_at") or "")
        stream = "codex-json"
    else:
        message = payload.rstrip()
        created_at = ""
        stream = "stdout"
    if not message:
        return
    record_raw_log_event(
        run_id=trace_run_id,
        agent_id=trace_agent_id,
        work_item_id=trace_work_item_id,
        tool_name="codex_exec",
        tool_call_id=codex_execution_id,
        seq=seq,
        level="info",
        stream=stream,
        message=message,
        created_at=created_at or None,
    )


def _codex_execution_lock_path(
    *,
    trace_run_dir: Path | None,
    default_dir: Path,
    codex_execution_id: str,
) -> Path | None:
    if not codex_execution_id:
        return None
    root = trace_run_dir or default_dir
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", codex_execution_id).strip("-")
    if not safe_id:
        return None
    lock_dir = root / ".agentic-codex-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{safe_id}.lock"


def _try_acquire_codex_execution_lock(lock_path: Path) -> int | None:
    try:
        return os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _wait_for_codex_execution_unlock(lock_path: Path, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        if not lock_path.exists():
            return True
        time.sleep(1)
    return not lock_path.exists()


def _record_duplicate_execution_suppressed(
    *,
    trace_run_dir: Path | None,
    trace_run_id: int | str,
    trace_agent_id: str,
    trace_work_item_id: str | None,
    codex_execution_id: str,
) -> None:
    if trace_run_dir is None or not trace_run_id or not trace_agent_id:
        return
    record_run_event(
        trace_run_dir,
        run_id=trace_run_id,
        agent_id=trace_agent_id,
        event_type="duplicate_execution_suppressed",
        status="suppressed",
        message="Duplicate Codex execution suppressed; existing execution lock is active.",
        work_item_id=trace_work_item_id,
        data={
            "codex_execution_id": codex_execution_id,
            "work_item_id": trace_work_item_id or "",
        },
    )


def _record_codex_progress_event(
    event: dict[str, object],
    *,
    trace_run_dir: Path | None,
    trace_run_id: int | str,
    trace_agent_id: str,
    trace_work_item_id: str | None,
) -> None:
    """Mirror operator-meaningful Codex stream events into canonical run trace."""

    if trace_run_dir is None or not trace_run_id or not trace_agent_id:
        return

    item = _codex_event_item(event)
    item_type = _codex_item_type(item)
    event_type = _codex_event_type(event)
    recorded_at = str(event.get("recorded_at") or "")
    item_id = str(item.get("id") or "")

    if event_type == "item.completed" and item_type == "agent_message":
        text = str(item.get("text") or "").strip()
        if not text:
            return
        record_run_event(
            trace_run_dir,
            run_id=trace_run_id,
            agent_id=trace_agent_id,
            event_type="codex_agent_message",
            status="in_progress",
            message=text,
            work_item_id=trace_work_item_id,
            data={
                "codex_item_id": item_id,
                "codex_event_type": event_type,
                "message": text,
            },
            created_at=recorded_at or None,
        )
        return

    # Command executions stay in raw Codex logs for developer debugging. Product
    # activity should show only the agent's user-facing progress commentary.


def _codex_event_item(event: dict[str, object]) -> dict[str, object]:
    params = event.get("params") if isinstance(event.get("params"), dict) else {}
    item = params.get("item") if isinstance(params.get("item"), dict) else event.get("item")
    return item if isinstance(item, dict) else {}


def _codex_event_type(event: dict[str, object]) -> str:
    return (
        str(event.get("method") or event.get("type") or "")
        .replace("/", ".")
        .replace("agentMessage", "agent_message")
        .replace("commandExecution", "command_execution")
    )


def _codex_item_type(item: dict[str, object]) -> str:
    return (
        str(item.get("type") or "")
        .replace("agentMessage", "agent_message")
        .replace("commandExecution", "command_execution")
    )


def codex_sandbox_from_env(default: str) -> str:
    configured = os.getenv(CODEX_SANDBOX_ENV, "").strip()
    if not configured:
        return default
    if configured not in VALID_CODEX_SANDBOXES:
        allowed = ", ".join(sorted(VALID_CODEX_SANDBOXES))
        raise ValueError(f"{CODEX_SANDBOX_ENV} must be one of: {allowed}")
    return configured


def _workspace_network_enabled() -> bool:
    """Whether workspace-write agents may reach the network (on by default).

    Codex disables outbound network in workspace-write mode; delivery agents (QA,
    handoff, planning) need it to fetch resources and verify deployed public URLs.
    """

    return os.getenv(CODEX_WORKSPACE_NETWORK_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def codex_runtime_config_summary() -> str:
    """One-line Codex worker sandbox/network summary for console startup logs."""

    network = "enabled" if _workspace_network_enabled() else "disabled"
    browsers = _anchor_repo_relative(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) or "unset"
    return (
        f"sandbox-default={DEFAULT_CODEX_SANDBOX} "
        f"workspace-write-network={network} qa-browsers={browsers}"
    )


def codex_exec_config_args_from_env() -> list[str]:
    config_args = [
        "--config",
        f'model_reasoning_effort="{codex_reasoning_effort_from_env()}"',
    ]
    service_tier = codex_service_tier_from_env()
    if service_tier == "fast":
        config_args.extend(["--config", 'service_tier="fast"'])

    inherit_env = os.getenv(CODEX_INHERIT_ENV_ENV, "1").strip().lower()
    if inherit_env in {"0", "false", "no", "off"}:
        return config_args
    return [
        *config_args,
        "--config",
        "shell_environment_policy.inherit=all",
    ]


def codex_reasoning_effort_from_env() -> str:
    configured = os.getenv(CODEX_REASONING_EFFORT_ENV, DEFAULT_CODEX_REASONING_EFFORT).strip()
    if not configured:
        return DEFAULT_CODEX_REASONING_EFFORT
    if configured not in VALID_CODEX_REASONING_EFFORTS:
        allowed = ", ".join(sorted(VALID_CODEX_REASONING_EFFORTS))
        raise ValueError(f"{CODEX_REASONING_EFFORT_ENV} must be one of: {allowed}")
    return configured


def codex_service_tier_from_env() -> str:
    configured = os.getenv(CODEX_SERVICE_TIER_ENV, DEFAULT_CODEX_SERVICE_TIER).strip()
    if not configured:
        return DEFAULT_CODEX_SERVICE_TIER
    if configured not in VALID_CODEX_SERVICE_TIERS:
        allowed = ", ".join(sorted(VALID_CODEX_SERVICE_TIERS))
        raise ValueError(f"{CODEX_SERVICE_TIER_ENV} must be one of: {allowed}")
    return configured


def build_codex_exec_environment(target_project_dir: Path) -> dict[str, str]:
    """Build the environment inherited by Codex specialist subprocesses."""

    env = _codex_subprocess_env()
    _merge_run_local_env(env, target_project_dir)
    if _uses_extension_binary_mode(env):
        env.pop(CODEX_API_KEY_ENV, None)
    elif not env.get(CODEX_API_KEY_ENV, "").strip():
        raise RuntimeError(
            f"{CODEX_API_KEY_ENV} is required for npm Codex CLI execution. "
            "Set it explicitly in the repo .env or process environment."
        )
    if not env.get(CODEX_API_KEY_ENV, "").strip():
        env.pop(CODEX_API_KEY_ENV, None)
    cache_root = Path(
        env.get("AGENTIC_RUNTIME_CACHE_DIR", str(target_project_dir.parent / ".agentic-cache"))
    )
    env.setdefault(
        "UV_CACHE_DIR",
        env.get(CODEX_UV_CACHE_DIR_ENV, str(cache_root / "uv")),
    )
    env.setdefault(
        "UV_PROJECT_ENVIRONMENT",
        str(cache_root / "venv"),
    )
    env.setdefault(
        "DENO_DIR",
        env.get(CODEX_DENO_DIR_ENV, str(cache_root / "deno")),
    )
    env.setdefault(
        "npm_config_cache",
        env.get(CODEX_NPM_CACHE_ENV, str(cache_root / "npm")),
    )
    _anchor_qa_browser_paths(env)
    return env


def _anchor_repo_relative(value: str) -> str:
    """Resolve a repo-relative path to an absolute path under the repo root."""

    value = value.strip()
    if not value or os.path.isabs(value):
        return value
    repo_root = Path(__file__).resolve().parents[4]
    return str((repo_root / value).resolve())


def _anchor_qa_browser_paths(env: dict[str, str]) -> None:
    """Make the QA browser-runtime paths absolute so a worker cwd change is safe.

    The QA worker runs with its cwd set to the generated project. A relative
    ``PLAYWRIGHT_BROWSERS_PATH`` (e.g. ``ops/qa-runtime/browsers`` as written by
    older agentic-qa-setup runs) would otherwise resolve against that cwd and make
    Playwright re-install a ~700 MB browser *inside the deliverable* — bloating the
    artifact and polluting deployment. Anchor any relative value to the repo root so
    the pre-installed runtime is reused instead.
    """

    for var in ("PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH"):
        if env.get(var, "").strip():
            env[var] = _anchor_repo_relative(env[var])


def _merge_run_local_env(env: dict[str, str], target_project_dir: Path) -> None:
    """Allow web-console run-local agent env files to configure Codex subprocesses.

    Provider credentials are intentionally read from the run-level delivery
    directory, not from generated-project/.env. The generated project folder is a
    deliverable artifact and must not become the carrier for platform secrets.
    """

    for env_path in (
        target_project_dir / "delivery" / "agent-runtime.env",
        target_project_dir.parent / "delivery" / "agent-runtime.env",
    ):
        if not env_path.exists():
            continue
        for key, value in _read_env_file(env_path).items():
            env[key] = value


def _uses_extension_binary_mode(env: dict[str, str]) -> bool:
    mode = env.get(AGENTIC_CODEX_BINARY_MODE_ENV, "").strip().lower()
    if mode == "extension":
        return True
    configured_binary = env.get("CODEX_BINARY", "").replace("\\", "/").lower()
    return bool(
        configured_binary
        and (
            "/.vscode/extensions/openai.chatgpt-" in configured_binary
            or "/.cursor/extensions/openai.chatgpt-" in configured_binary
        )
    )


def _codex_env_passthrough_extra() -> frozenset[str]:
    raw = os.getenv(CODEX_ENV_PASSTHROUGH_ENV, "")
    return frozenset(part.strip().upper() for part in raw.split(",") if part.strip())


def _is_allowed_codex_env(name: str, extra: frozenset[str]) -> bool:
    upper = name.upper()
    if upper in _CODEX_ENV_ALLOWED_NAMES or upper in extra:
        return True
    return any(upper.startswith(prefix) for prefix in _CODEX_ENV_ALLOWED_PREFIXES)


def _codex_subprocess_env() -> dict[str, str]:
    """Build host-tool environment inherited by Codex specialist subprocesses.

    Only allowlisted host variables are inherited; the rest of the host
    environment (and any secrets it carries) is dropped before the worker starts.
    """

    extra = _codex_env_passthrough_extra()
    env = {name: value for name, value in os.environ.items() if _is_allowed_codex_env(name, extra)}
    _prepend_repo_local_node_to_path(env)
    if "AZURE_CONFIG_DIR" not in env:
        azure_config = Path.home() / ".azure"
        if azure_config.exists():
            env["AZURE_CONFIG_DIR"] = str(azure_config)

    plugin_dirs = _docker_plugin_dirs()
    if plugin_dirs and "DOCKER_CLI_PLUGIN_EXTRA_DIRS" not in env:
        env["DOCKER_CLI_PLUGIN_EXTRA_DIRS"] = os.pathsep.join(plugin_dirs)

    return env


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip():
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _prepend_repo_local_node_to_path(env: dict[str, str]) -> None:
    node_bin_dir = _repo_local_node_bin_dir()
    if node_bin_dir is None:
        return

    path_key = "Path" if os.name == "nt" else "PATH"
    existing_path = env.get(path_key) or env.get("PATH") or ""
    path_parts = [part for part in existing_path.split(os.pathsep) if part]
    if str(node_bin_dir) not in path_parts:
        env[path_key] = os.pathsep.join([str(node_bin_dir), *path_parts])


def _repo_local_node_bin_dir(repo_root: Path | None = None) -> Path | None:
    root = repo_root or Path(__file__).resolve().parents[4]
    node_root = root / "ops" / "codex-npm-smoke" / ".tools" / "node"
    if os.name == "nt":
        candidates = sorted(
            node_root.glob("node-v*-win-*"),
            key=lambda path: path.name,
            reverse=True,
        )
        for candidate in candidates:
            if (candidate / "node.exe").exists():
                return candidate
        return None

    candidates = sorted(node_root.glob("node-v*-linux-*"), key=lambda path: path.name, reverse=True)
    for candidate in candidates:
        bin_dir = candidate / "bin"
        if (bin_dir / "node").exists():
            return bin_dir
    return None


def _codex_host_tool_dirs() -> list[str]:
    """Directories Codex workers need for host CLI auth/plugins."""

    candidates = [
        Path.home() / ".azure",
        Path.home() / ".docker",
        *_docker_plugin_dirs_as_paths(),
    ]
    host_dirs: list[str] = []
    for candidate in candidates:
        if candidate.exists() and str(candidate) not in host_dirs:
            host_dirs.append(str(candidate))
    return host_dirs


def _docker_plugin_dirs() -> list[str]:
    plugin_dirs: list[str] = []
    for candidate in _docker_plugin_dirs_as_paths():
        if candidate and candidate.exists() and str(candidate) not in plugin_dirs:
            plugin_dirs.append(str(candidate))
    return plugin_dirs


def _docker_plugin_dirs_as_paths() -> list[Path]:
    return [
        *_docker_plugin_dirs_from_config(),
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Rancher Desktop"
        / "resources"
        / "resources"
        / "win32"
        / "docker-cli-plugins",
        Path.home() / ".docker" / "cli-plugins",
    ]


def _docker_plugin_dirs_from_config() -> list[Path]:
    config_path = Path.home() / ".docker" / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    dirs = config.get("cliPluginsExtraDirs", [])
    if not isinstance(dirs, list):
        return []
    return [Path(item) for item in dirs if isinstance(item, str) and item.strip()]


def _target_project_dir_from_command(command: Sequence[str]) -> Path:
    command_parts = list(command)
    try:
        cd_index = command_parts.index("--cd")
    except ValueError:
        return Path.cwd()
    try:
        return Path(command_parts[cd_index + 1])
    except IndexError:
        return Path.cwd()


def _is_codex_terminal_event(line: str) -> bool:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(event, dict):
        return False
    event_type = str(event.get("type") or event.get("method") or "").replace("/", ".")
    return event_type == "turn.completed"
