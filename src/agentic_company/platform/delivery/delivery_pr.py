"""Prepare the run's git working tree and hand repo context to the agents.

The platform does NOT touch git beyond this: the workers own branch/commit/push/
PR/merge/comment themselves (the ``git-pr-workflow`` skill), in-context, right after
writing the files. The platform only (a) clones an existing repo into the run
workspace at run start, and (b) tells each worker which repo/base branch is connected
so it triggers the skill and delivers via a PR.

generated-project IS the git working tree:
- existing repo (support): cloned into generated-project at run start, so the FS
  builds on top of it and each work item is a real diff;
- new repo: git-inited + remote created by the worker on its first push.
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("agentic_company.delivery_pr")

_REPO_ENSURED: set[str] = set()  # run_uids whose working tree is already prepared


def reset_repo_state(run_uid: str | None = None) -> None:
    """Forget ensured-repo state (finished runs / tests)."""
    if run_uid is None:
        _REPO_ENSURED.clear()
    else:
        _REPO_ENSURED.discard(run_uid)


def ensure_run_repo(run_uid: str, *, gh: Any = None, git: Any = None) -> None:
    """Run-start: clone an EXISTING repo into generated-project before the FS builds.

    New repos are inited lazily by the worker on its first push, so nothing happens
    here for them. No-op when the run has no repo host. Guarded — never breaks the run.
    """
    if run_uid in _REPO_ENSURED:
        return
    try:
        from agentic_company.platform.db.runtime_db import _repo_and_run
        from agentic_company.platform.delivery.repo_manager import build_run_repo

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is not None:
            adapter, spec = built
            if spec.mode == "support" and not (spec.target_dir / ".git").exists():
                spec.target_dir.parent.mkdir(parents=True, exist_ok=True)
                adapter.ensure_repo(spec)  # clone into the run workspace
                LOGGER.info("Cloned %s into the run workspace", spec.repository)
    except Exception as exc:  # best-effort: never block delivery on repo setup
        LOGGER.warning("Run repo ensure failed for %s: %s", run_uid, exc)
    _REPO_ENSURED.add(run_uid)  # mark either way so we don't retry per item


def run_repo_context(run_uid: str, *, gh: Any = None, git: Any = None) -> dict[str, str] | None:
    """Repo info to hand the agent ({repository, base_branch}), or None when no host.

    Lets the FS/Deploy/QA execution requests tell the worker a repo is connected so it
    triggers the git-pr-workflow skill and delivers via a PR.
    """
    try:
        from agentic_company.platform.db.runtime_db import _repo_and_run
        from agentic_company.platform.delivery.repo_manager import build_run_repo

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return None
        _adapter, spec = built
        return {"repository": spec.repository, "base_branch": spec.base_branch or "main"}
    except Exception:
        return None
