"""Build the source-repo adapter + RepoSpec for a run (mirror of run_mirror.py).

A run gets a GitHub repo adapter ONLY when its active ``github`` work-system
connection names a repository; otherwise code stays local and the run delivers
exactly as before (returns None). This is the REPO host, kept independent of the
BOARD host — code can live on GitHub while the board lives on Jira, etc. Each
adapter declares its capabilities so the orchestrator does what a host supports
and gracefully skips the rest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentic_company.ports.repo import RepoPort, RepoSpec

LOGGER = logging.getLogger("agentic_company.repo_manager")


def build_run_repo(
    repo: Any, db_run_id: int, *, gh: Any = None, git: Any = None
) -> tuple[RepoPort, RepoSpec] | None:
    """Return ``(repo_adapter, spec)`` for a run, or None when code stays local."""
    run = repo.get_run(db_run_id)
    if run is None:
        return None
    project_id = getattr(run, "project_id", None)
    conn = repo.get_active_work_system_connection(
        run_id=db_run_id, project_id=project_id, system="github"
    )
    if conn is None or not conn.repository.strip():
        return None  # no repo host configured -> local delivery only

    from agentic_company.integrations.github.cli import GhRunner
    from agentic_company.integrations.github.repo import GitHubRepoAdapter, GitRunner

    adapter = GitHubRepoAdapter(gh=gh or GhRunner(), git=git or GitRunner())
    return adapter, _repo_spec(run, conn)


def _repo_spec(run: Any, conn: Any) -> RepoSpec:
    target = (
        Path(run.target_project_dir)
        if getattr(run, "target_project_dir", "")
        else Path(run.run_dir) / "generated-project"
    )
    metadata = getattr(conn, "metadata", None) or {}
    # 'support' clones an existing repo into generated-project; 'new' creates one.
    # Default to support (the operator points ADL at a repo they own); the board
    # UI can set repo_mode='new' to have ADL create it.
    mode = str(metadata.get("repo_mode") or "support")
    base_branch = str(getattr(conn, "default_branch", "") or "main")
    return RepoSpec(
        mode=mode,
        target_dir=target,
        repository=conn.repository.strip(),
        base_branch=base_branch,
        private=True,
    )
