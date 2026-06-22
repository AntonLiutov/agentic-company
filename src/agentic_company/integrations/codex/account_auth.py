"""User-scoped Codex account authentication helpers."""

from __future__ import annotations

import json
import os
import re
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
    # MUST be absolute: we pass this as CODEX_HOME *and* as the subprocess cwd, and codex
    # resolves a relative CODEX_HOME against that cwd — doubling the path (".../users/3/
    # .../users/3") so it "does not exist" and `codex login` dies before printing its URL.
    # Resolve here so a relative AGENTIC_CODEX_AUTH_ROOT (e.g. "data/codex-auth") is safe.
    configured = os.getenv(CODEX_AUTH_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path.cwd() / "data" / "codex-auth").resolve()


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


def start_codex_login(user_id: int, *, device: bool) -> dict[str, str]:
    """Start ``codex login`` and capture its output.

    ``device=False`` is the browser OAuth flow (like VS Code / Claude Code): codex opens
    the user's browser and completes via a localhost callback — no code typing. Use it
    wherever a browser is available (the ``local`` runtime profile). ``device=True`` is
    the device-code fallback (``--device-auth``) for a headless/remote host (``vm_mvp``),
    where there is no browser to open on the server, so the user enters a one-time code.
    """

    codex_home = ensure_codex_home_for_user(user_id)
    state_path = codex_home / DEVICE_LOGIN_STATE
    state = {
        "status": "started",
        "message": "Opening your browser to sign in to Codex…"
        if not device
        else "Starting sign-in…",
        "flow": "device" if device else "browser",
        "auth_url": "",
        "user_code": "",
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    _chmod_best_effort(state_path, 0o600)
    env = _codex_auth_env(codex_home)
    command = [resolve_codex_binary(), "login"]
    if device:
        command.append("--device-auth")
    process = subprocess.Popen(
        command,
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
        name=f"codex-login-{user_id}",
        daemon=True,
    )
    thread.start()
    return state


def start_codex_device_login(user_id: int) -> dict[str, str]:
    """Back-compat: the device-code flow (headless hosts). Prefer ``start_codex_login``."""
    return start_codex_login(user_id, device=True)


def codex_device_login_state(user_id: int) -> dict[str, str]:
    state_path = codex_home_for_user(user_id) / DEVICE_LOGIN_STATE
    if not state_path.exists():
        return {"status": "idle", "message": "No Codex login in progress."}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown", "message": "Could not read Codex login state."}
    return {str(key): str(value) for key, value in payload.items()}


def _capture_device_login(process: subprocess.Popen[str], state_path: Path) -> None:
    output_parts: list[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            output_parts.append(line.rstrip())
            _write_device_state(state_path, "running", output_parts)
    code = process.wait()
    _write_device_state(state_path, "completed" if code == 0 else "failed", output_parts)


_URL_RE = re.compile(r"https://\S+")
# Device user codes are 4-then-4..8 upper/digit (codex 0.141 prints e.g. 0SAE-KWF9Q = 4-5).
# Still tight enough to never match a fragment of the browser-flow oauth/authorize URL
# (its code_challenge/state are mixed-case).
_USER_CODE_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,8}\b")
# codex 0.141 COLORS its login output (URL + code wrapped in \x1b[94m…\x1b[0m). Strip ANSI
# escapes before parsing: otherwise \S+ swallows the trailing reset into the URL (a broken
# link) and the ANSI bytes hugging the code break the \b word boundary so it never matches.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _parse_login_output(output: str) -> dict[str, str]:
    """Pull the human-usable bits out of raw ``codex login`` output.

    The raw CLI text is unfit for a friend on the web console: a giant query-string URL,
    "navigate to this URL", "use codex login --device-auth". We extract only what a person
    needs — the page to open and (device flow) the short code to type — so the template can
    render a clean button + code box instead of dumping the terminal.
    """
    output = _ANSI_RE.sub("", output)
    code_match = _USER_CODE_RE.search(output)
    user_code = code_match.group(0) if code_match else ""
    urls = [u.rstrip(".,)") for u in _URL_RE.findall(output)]
    # Prefer the short human verification page (…/device, …/activate) over the long
    # oauth/authorize callback URL; fall back to the first https URL we saw.
    verification = next((u for u in urls if "/device" in u or "/activate" in u), "")
    auth_url = verification or (urls[0] if urls else "")
    return {"auth_url": auth_url, "user_code": user_code}


def _login_message(flow: str, status: str, parsed: dict[str, str]) -> str:
    if status == "completed":
        return "Codex is connected."
    if status == "failed":
        return "Sign-in did not complete. Try again."
    if flow == "device":
        if parsed["user_code"]:
            return "Enter the code to finish signing in."
        return "Starting sign-in…"
    return "Finish sign-in in your browser."


def _write_device_state(state_path: Path, status: str, output_parts: list[str]) -> None:
    output = "\n".join(output_parts)
    parsed = _parse_login_output(output)
    try:
        existing = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    flow = str(existing.get("flow", ""))
    payload = {
        "status": status,
        "flow": flow,
        "message": _login_message(flow, status, parsed),
        "auth_url": parsed["auth_url"],
        "user_code": parsed["user_code"],
    }
    if existing.get("pid"):
        payload["pid"] = existing["pid"]
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
    # The standalone codex is a Node shim (codex.cmd) — without the bundled node on PATH it
    # fails before opening the browser ("'node' is not recognized"). Mirror the worker env so
    # console-side `codex login` / `login status` find node too.
    try:
        from agentic_company.integrations.codex.runner import _prepend_repo_local_node_to_path

        _prepend_repo_local_node_to_path(allowed)
    except Exception:
        pass
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
