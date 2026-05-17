"""Codex CLI discovery helpers."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

AGENTIC_CODEX_ALLOW_EXTENSION_BINARY_ENV = "AGENTIC_CODEX_ALLOW_EXTENSION_BINARY"
AGENTIC_CODEX_BINARY_MODE_ENV = "AGENTIC_CODEX_BINARY_MODE"
VALID_CODEX_BINARY_MODES = {"auto", "npm", "extension"}


def resolve_codex_binary(
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    repo_root: Path | None = None,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> str:
    environment = os.environ if env is None else env
    configured = environment.get("CODEX_BINARY")
    if configured:
        return configured

    mode = _codex_binary_mode(environment)
    if mode == "extension":
        return _extension_codex_binary(environment, home)

    local_npm_binary = _repo_local_npm_codex_binary(repo_root)
    if local_npm_binary:
        return str(local_npm_binary)
    if mode == "npm":
        return "codex"

    on_path = path_lookup("codex")
    if on_path:
        return on_path

    if environment.get(AGENTIC_CODEX_ALLOW_EXTENSION_BINARY_ENV, "").strip() != "1":
        return "codex"

    return _extension_codex_binary(environment, home)


def _codex_binary_mode(environment: dict[str, str]) -> str:
    configured = environment.get(AGENTIC_CODEX_BINARY_MODE_ENV, "auto").strip().lower()
    if not configured:
        return "auto"
    if configured not in VALID_CODEX_BINARY_MODES:
        allowed = ", ".join(sorted(VALID_CODEX_BINARY_MODES))
        raise ValueError(f"{AGENTIC_CODEX_BINARY_MODE_ENV} must be one of: {allowed}")
    return configured


def _extension_codex_binary(environment: dict[str, str], home: Path | None) -> str:
    user_home = home or Path.home()
    for extension_root_name in [".vscode", ".cursor"]:
        extension_root = user_home / extension_root_name / "extensions"
        candidates = sorted(
            extension_root.glob("openai.chatgpt-*"),
            key=lambda path: path.name,
            reverse=True,
        )
        for candidate in candidates:
            executable = candidate / "bin" / "windows-x86_64" / "codex.exe"
            if executable.exists():
                return str(executable)

    return "codex"


def _repo_local_npm_codex_binary(repo_root: Path | None) -> Path | None:
    root = repo_root or _default_repo_root()
    install_bin = root / "ops" / "codex-npm-smoke" / ".codex-npm" / "node_modules" / ".bin"
    candidates = [install_bin / "codex.cmd", install_bin / "codex"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]
