"""Codex CLI process execution helpers."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.integrations.codex.cli import (
    AGENTIC_CODEX_BINARY_MODE_ENV,
    resolve_codex_binary,
)
from agentic_company.integrations.codex.events import append_raw_codex_event
from agentic_company.integrations.commands import StreamedCommand, stream_command

CODEX_SANDBOX_ENV = "AGENTIC_CODEX_SANDBOX"
CODEX_INHERIT_ENV_ENV = "AGENTIC_CODEX_INHERIT_ENV"
CODEX_UV_CACHE_DIR_ENV = "AGENTIC_CODEX_UV_CACHE_DIR"
CODEX_DENO_DIR_ENV = "AGENTIC_CODEX_DENO_DIR"
CODEX_NPM_CACHE_ENV = "AGENTIC_CODEX_NPM_CACHE"
CODEX_REASONING_EFFORT_ENV = "AGENTIC_CODEX_REASONING_EFFORT"
CODEX_SERVICE_TIER_ENV = "AGENTIC_CODEX_SERVICE_TIER"
CODEX_API_KEY_ENV = "CODEX_API_KEY"
DEFAULT_CODEX_MODEL = "gpt-5.3-codex"
DEFAULT_CODEX_SANDBOX = "danger-full-access"
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
) -> list[str]:
    """Build the low-level `codex exec` command."""

    binary = codex_binary or resolve_codex_binary()
    effective_sandbox = (
        sandbox or DEFAULT_CODEX_SANDBOX
        if force_sandbox
        else codex_sandbox_from_env(sandbox or DEFAULT_CODEX_SANDBOX)
    )
    command = [
        binary,
        "exec",
        *codex_exec_config_args_from_env(),
        "--model",
        model,
        "--sandbox",
        effective_sandbox,
        "--cd",
        target_project_dir,
        "--add-dir",
        str(run_dir),
    ]
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


def stream_codex_exec_to_log(
    command: Sequence[str],
    prompt: str,
    timeout_seconds: int,
    log_path: Path,
    raw_events_path: Path,
    env: dict[str, str] | None = None,
    codex_execution_id: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run Codex while streaming command output and raw JSON events to artifacts."""

    raw_events_path.write_text("", encoding="utf-8")
    log_path.write_text(f"timeout_seconds={timeout_seconds}\n\n", encoding="utf-8")
    command_env = env or build_codex_exec_environment(_target_project_dir_from_command(command))
    return stream_command(
        StreamedCommand(
            command=command,
            cwd=log_path.parent,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
            input_text=prompt,
            env=command_env,
            on_stdout_line=lambda line: append_raw_codex_event(
                raw_events_path,
                line,
                metadata={"codex_execution_id": codex_execution_id},
            ),
            terminal_output_predicate=_is_codex_terminal_event,
        )
    )


def codex_sandbox_from_env(default: str) -> str:
    configured = os.getenv(CODEX_SANDBOX_ENV, "").strip()
    if not configured:
        return default
    if configured not in VALID_CODEX_SANDBOXES:
        allowed = ", ".join(sorted(VALID_CODEX_SANDBOXES))
        raise ValueError(f"{CODEX_SANDBOX_ENV} must be one of: {allowed}")
    return configured


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
    env.setdefault(
        "UV_CACHE_DIR",
        env.get(CODEX_UV_CACHE_DIR_ENV, str(target_project_dir / ".uv-cache")),
    )
    env.setdefault(
        "DENO_DIR",
        env.get(CODEX_DENO_DIR_ENV, str(target_project_dir / ".deno-cache")),
    )
    env.setdefault(
        "npm_config_cache",
        env.get(CODEX_NPM_CACHE_ENV, str(target_project_dir / ".npm-cache")),
    )
    return env


def _merge_run_local_env(env: dict[str, str], target_project_dir: Path) -> None:
    """Allow web-console run-local env files to configure Codex subprocesses."""

    for env_path in (
        target_project_dir / ".env",
        target_project_dir / "generated-project" / ".env",
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


def _codex_subprocess_env() -> dict[str, str]:
    """Build host-tool environment inherited by Codex specialist subprocesses."""

    env = dict(os.environ)
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
