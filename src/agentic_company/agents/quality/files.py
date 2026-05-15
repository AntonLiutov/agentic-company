"""Filesystem helpers for QA checks and handoff evidence."""

from __future__ import annotations

from pathlib import Path


def iter_project_files(target_dir: Path) -> list[Path]:
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
    ignored_names = {".env", "uv.lock"}
    return [
        path
        for path in sorted(target_dir.rglob("*"))
        if path.is_file()
        and path.name not in ignored_names
        and not any(part in ignored_parts for part in path.relative_to(target_dir).parts)
    ]


def read_text_safely(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""
