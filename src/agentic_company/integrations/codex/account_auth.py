"""User-scoped Codex account authentication helpers."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from agentic_company.integrations.codex.cli import resolve_codex_binary

CODEX_AUTH_ROOT_ENV = "AGENTIC_CODEX_AUTH_ROOT"
CODEX_HOME_ENV = "CODEX_HOME"
DEVICE_LOGIN_STATE = ".device-login.json"


@dataclass(frozen=True, slots=True)
class CodexLoginStatus:
    connected: bool
    auth_mode: str = ""
    message: str = ""


def codex_auth_root() -> Path:
    configured = os.getenv(CODEX_AUTH_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "codex-auth"


def codex_home_for_user(user_id: int) -> Path:
    return codex_auth_root() / "users" / str(user_id)


def ensure_codex_home_for_user(user_id: int) -> Path:
    path = codex_home_for_user(user_id)
    path.mkdir(parents=True, exist_ok=True)
    _chmod_best_effort(path, 0o700)
    auth_file = path / "auth.json"
    if auth_file.exists():
        _chmod_best_effort(auth_file, 0o600)
    return path


def delete_codex_home_for_user(user_id: int) -> None:
    path = codex_home_for_user(user_id)
    root = codex_auth_root().resolve()
    target = path.resolve()
    if root == target or root not in target.parents:
        raise ValueError(f"Refusing to delete unexpected Codex auth path: {target}")
    shutil.rmtree(target, ignore_errors=True)


def codex_login_status(user_id: int, *, timeout_seconds: int = 10) -> CodexLoginStatus:
    codex_home = ensure_codex_home_for_user(user_id)
    env = _codex_auth_env(codex_home)
    try:
        completed = subprocess.run(
            [resolve_codex_binary(), "login", "status"],
            cwd=codex_home,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CodexLoginStatus(False, message=str(exc))
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    auth_mode = _auth_mode_from_file(codex_home / "auth.json")
    return CodexLoginStatus(
        completed.returncode == 0,
        auth_mode=auth_mode,
        message=output or ("logged in" if completed.returncode == 0 else "not logged in"),
    )


def start_codex_device_login(user_id: int) -> dict[str, str]:
    """Start ``codex login --device-auth`` and capture its device-code output."""

    codex_home = ensure_codex_home_for_user(user_id)
    state_path = codex_home / DEVICE_LOGIN_STATE
    state = {
        "status": "started",
        "message": "Codex device login started.",
        "output": "",
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _chmod_best_effort(state_path, 0o600)
    env = _codex_auth_env(codex_home)
    process = subprocess.Popen(
        [resolve_codex_binary(), "login", "--device-auth"],
        cwd=codex_home,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    state["pid"] = str(process.pid)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    thread = threading.Thread(
        target=_capture_device_login,
        args=(process, state_path),
        name=f"codex-device-login-{user_id}",
        daemon=True,
    )
    thread.start()
    return state


def codex_device_login_state(user_id: int) -> dict[str, str]:
    state_path = codex_home_for_user(user_id) / DEVICE_LOGIN_STATE
    if not state_path.exists():
        return {"status": "idle", "message": "No Codex login in progress.", "output": ""}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown", "message": "Could not read Codex login state.", "output": ""}
    return {str(key): str(value) for key, value in payload.items()}


def _capture_device_login(process: subprocess.Popen[str], state_path: Path) -> None:
    output_parts: list[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            output_parts.append(line.rstrip())
            _write_device_state(state_path, "running", output_parts)
    code = process.wait()
    _write_device_state(state_path, "completed" if code == 0 else "failed", output_parts)


def _write_device_state(state_path: Path, status: str, output_parts: list[str]) -> None:
    payload = {
        "status": status,
        "message": "Codex device login is running."
        if status == "running"
        else "Codex device login finished."
        if status == "completed"
        else "Codex device login failed.",
        "output": "\n".join(output_parts),
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _codex_auth_env(codex_home: Path) -> dict[str, str]:
    allowed = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        }
    }
    allowed[CODEX_HOME_ENV] = str(codex_home)
    return allowed


def _auth_mode_from_file(auth_file: Path) -> str:
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    mode = str(payload.get("auth_mode") or "").strip()
    return mode


def _chmod_best_effort(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return
    if os.name == "nt" and path.is_file():
        try:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            return
