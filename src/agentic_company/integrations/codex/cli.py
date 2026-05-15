"""Codex CLI discovery helpers."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path


def resolve_codex_binary(
    *,
    env: dict[str, str] | None = None,
    home: Path | None = None,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> str:
    environment = env or os.environ
    configured = environment.get("CODEX_BINARY")
    if configured:
        return configured

    on_path = path_lookup("codex")
    if on_path:
        return on_path

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
