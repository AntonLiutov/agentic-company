"""Build the external-board mirror for a run (best-effort, off the critical path).

A run mirrors its work-item progress onto an external board only when it has an
active ``github`` work-system connection with a repository; otherwise the board
is ADL's own Postgres and there is nothing external to mirror (returns None).

When the connection names a GitHub Project, the board ids (project node id,
Status field id + option ids) are read from the connection's ``metadata`` if the
console cached them at setup; otherwise they are resolved live once and the
Status columns are ensured. Everything here is wrapped by the caller so a GitHub
outage can never break a run.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_company.platform.board_selection import select_board
from agentic_company.platform.work_mirror import WorkMirror

LOGGER = logging.getLogger("agentic_company.run_mirror")

# Built once per run (the connection + board ids are stable for the run).
_RUN_MIRRORS: dict[int, WorkMirror | None] = {}


def get_run_mirror(repo: Any, db_run_id: int, *, gh: Any = None) -> WorkMirror | None:
    """Return the cached mirror for a run, building it on first use."""
    if db_run_id not in _RUN_MIRRORS:
        try:
            _RUN_MIRRORS[db_run_id] = build_run_mirror(repo, db_run_id, gh=gh)
        except Exception as exc:  # never let mirror setup break a run
            LOGGER.warning("Run mirror build failed for run %s: %s", db_run_id, exc)
            _RUN_MIRRORS[db_run_id] = None
    return _RUN_MIRRORS[db_run_id]


def reset_run_mirror(db_run_id: int | None = None) -> None:
    """Forget cached mirror(s) — for tests and connection changes."""
    if db_run_id is None:
        _RUN_MIRRORS.clear()
    else:
        _RUN_MIRRORS.pop(db_run_id, None)


def build_run_mirror(repo: Any, db_run_id: int, *, gh: Any = None) -> WorkMirror | None:
    """Construct the board mirror for a run, or None when no external board."""
    run = repo.get_run(db_run_id)
    project_id = getattr(run, "project_id", None)
    conn = repo.get_active_work_system_connection(
        run_id=db_run_id, project_id=project_id, system="github"
    )
    if conn is None or not conn.repository.strip():
        return None  # internal board is the source of truth -> nothing to mirror

    if gh is None:
        from agentic_company.integrations.github.cli import GhRunner

        gh = GhRunner()

    repository = conn.repository.strip()
    md = dict(conn.metadata or {})
    owner = str(md.get("owner", "")).strip() or repository.split("/", 1)[0]
    number = str(md.get("project_number", "")).strip()
    node = str(md.get("project_id", "")).strip()
    field_id = str(md.get("status_field_id", "")).strip()
    options = md.get("status_options") or {}

    # First run for this ADL project: no board ids cached yet. Provision a fresh
    # board (or resolve a previously-created one) and persist the ids back onto
    # the connection so every later run reuses the same board.
    if not (node and field_id and options):
        if number:
            node, field_id, options = _resolve_board(gh, owner, number)
        else:
            number, node, field_id, options = _provision_board(
                gh, repo, project_id, owner, repository
            )
        md.update(
            owner=owner,
            project_number=number,
            project_id=node,
            status_field_id=field_id,
            status_options=options,
        )
        try:
            repo.update_work_system_connection_metadata(conn.id, md)
        except Exception as exc:  # caching is an optimisation; never fatal
            LOGGER.warning("Could not persist board ids for run %s: %s", db_run_id, exc)

    board = select_board(
        store=repo,
        run_id=db_run_id,
        system="github",
        repository=repository,
        connection_id=conn.id,
        gh=gh,
        owner=owner,
        project_number=number,
        project_id=node,
        status_field_id=field_id,
        status_options=options,
    )
    return WorkMirror(board)


def _resolve_board(gh: Any, owner: str, number: str) -> tuple[str, str, dict[str, str]]:
    from agentic_company.integrations.github.projects import (
        ensure_status_columns,
        resolve_project_board,
    )

    resolved = resolve_project_board(gh, owner=owner, project_number=number)
    options = dict(resolved.status_options)
    if resolved.status_field_id:
        mapping, _ = ensure_status_columns(gh, status_field_id=resolved.status_field_id)
        options = mapping or options
    return resolved.project_id, resolved.status_field_id, options


def _provision_board(
    gh: Any, repo: Any, project_id: int | None, owner: str, repository: str
) -> tuple[str, str, str, dict[str, str]]:
    from agentic_company.integrations.github.projects import provision_project_board

    title = _board_title(repo, project_id, repository)
    number, resolved = provision_project_board(gh, owner=owner, repository=repository, title=title)
    return number, resolved.project_id, resolved.status_field_id, resolved.status_options


def _board_title(repo: Any, project_id: int | None, repository: str) -> str:
    if project_id is not None:
        try:
            project = repo.get_project(project_id)
            if project is not None and project.name.strip():
                return f"ADL · {project.name.strip()}"
        except Exception:  # fall back to the repo name
            pass
    return f"ADL · {repository.split('/', 1)[-1]}"
