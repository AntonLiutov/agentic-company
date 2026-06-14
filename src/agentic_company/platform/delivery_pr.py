"""Publish FS/Deploy work-item output to the repo host as a branch + PR.

Strictly best-effort and guarded: a repo/PR failure must NEVER break delivery
(the run delivers locally regardless). The repo host is independent of the board
host (RepoPort), so a host that can't open PRs just gets its branch pushed and the
branch linked on the card — "do what's possible, skip the rest".

generated-project IS the git working tree:
- existing repo (support): cloned into generated-project at run start, so the FS
  builds on top of it and each work item is a real diff;
- new repo: git-inited + remote created on the first publish (that item seeds
  main); later items get their own branch + PR.
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("agentic_company.delivery_pr")

# Only the agents that produce committable code/config open PRs.
_PR_OWNER_AGENTS = {"fullstack-agent", "deployment-agent"}
_REPO_ENSURED: set[str] = set()  # run_uids whose working tree is already prepared


def should_publish_pr(owner_agent: str) -> bool:
    """True only for code/config producers (Builder, Publisher) — not planners/QA."""
    return str(owner_agent or "").strip() in _PR_OWNER_AGENTS


def reset_repo_state(run_uid: str | None = None) -> None:
    """Forget ensured-repo state (finished runs / tests)."""
    if run_uid is None:
        _REPO_ENSURED.clear()
    else:
        _REPO_ENSURED.discard(run_uid)


def ensure_run_repo(run_uid: str, *, gh: Any = None, git: Any = None) -> None:
    """Run-start: clone an EXISTING repo into generated-project before the FS builds.

    New repos are inited lazily on the first publish, so nothing happens here for
    them. No-op when the run has no repo host. Guarded — never breaks the run.
    """
    if run_uid in _REPO_ENSURED:
        return
    try:
        from agentic_company.platform.repo_manager import build_run_repo
        from agentic_company.platform.runtime_db import _repo_and_run

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


def publish_work_item_pr(
    run_uid: str, work_item_id: str, *, title: str = "", gh: Any = None, git: Any = None
) -> str:
    """After a Builder/Publisher item is built: branch -> commit -> push -> PR.

    Returns the PR url, or '' when there's no repo host / no changes / the host
    can't open PRs. Never raises into delivery.
    """
    try:
        from agentic_company.platform.repo_manager import build_run_repo
        from agentic_company.platform.runtime_db import _repo_and_run, get_work_item

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return ""  # code stays local
        adapter, spec = built
        target = spec.target_dir
        if not target.exists():
            return ""  # the worker produced nothing locally

        just_seeded = False
        if not (target / ".git").exists():
            if spec.mode == "new":
                adapter.ensure_repo(spec)  # init + create remote + push baseline
                just_seeded = True
            else:
                return ""  # support repo wasn't cloned at start -> skip gracefully
        if just_seeded:
            return ""  # the first item seeds main; PRs start from the next item

        item = get_work_item(run_uid, work_item_id)
        label = title or getattr(item, "title", "") or work_item_id
        if not adapter.capabilities.pull_request:
            adapter.commit_push(target, f"chore({work_item_id}): {label}")  # branch only
            return ""

        branch = f"adl/{work_item_id.lower()}"
        adapter.create_branch(target, branch, base=spec.base_branch)
        adapter.commit_push(target, f"feat({work_item_id}): {label}", branch=branch)
        pr = adapter.open_pr(
            target,
            title=f"[{work_item_id}] {label}",
            body=f"Delivers `{work_item_id}` — {label}.\n\n_Opened by Agentic Delivery Lab._",
            base=spec.base_branch,
            head=branch,
        )
        if pr.url:
            _mirror_pr(run_uid, work_item_id, pr.url, pr.number)
        return pr.url
    except Exception as exc:  # best-effort: never break delivery
        LOGGER.warning("PR publish failed for %s: %s", work_item_id, exc)
        return ""


def _mirror_pr(run_uid: str, work_item_id: str, pr_url: str, pr_id: str) -> None:
    from agentic_company.platform.runtime_db import _submit_pr_mirror

    _submit_pr_mirror(run_uid, work_item_id, pr_url, pr_id)
