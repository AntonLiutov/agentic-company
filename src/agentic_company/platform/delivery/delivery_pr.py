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

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("agentic_company.delivery_pr")

# Per-run map of work_item_id -> {url, number, branch, merged}. Persisted in the run
# workspace so later agent workers can learn the PR exists. Git operations stay
# agent-owned through the git-pr-workflow skill; the platform only mirrors records.
_PR_STORE_REL = "delivery/work-item-prs.json"

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
        from agentic_company.platform.delivery.repo_manager import build_run_repo
        from agentic_company.platform.db.runtime_db import _repo_and_run

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
    """Mirror the PR the AGENT opened for this work item onto its board card.

    Git is owned by the agent (the ``git-pr-workflow`` skill): the worker branches,
    commits, pushes, and opens/updates the PR itself, in-context, right after writing
    the files — so the fragile platform branch dance (``checkout -b`` on a dirty tree)
    is gone. The platform only DETECTS that PR by the ``adl/<id>`` branch convention
    and records + links it on the card. Returns the PR url, or '' when there is no
    repo host or the agent has not opened a PR. Never raises into delivery.
    """
    try:
        from agentic_company.platform.delivery.repo_manager import build_run_repo
        from agentic_company.platform.db.runtime_db import _repo_and_run

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return ""  # no repo host -> code stays local
        adapter, spec = built
        target = spec.target_dir
        find_pr = getattr(adapter, "find_pr", None)
        if not (callable(find_pr) and target.exists()):
            return ""
        branch = f"adl/{work_item_id.lower()}"
        pr = find_pr(target, branch)
        if pr and pr.url:
            record_work_item_pr(run_uid, work_item_id, pr.url, pr.number, branch)
            _mirror_pr(run_uid, work_item_id, pr.url, pr.number)
            return pr.url
        return ""  # the agent has not opened a PR for this branch (yet)
    except Exception as exc:  # best-effort: never break delivery
        LOGGER.warning("PR mirror failed for %s: %s", work_item_id, exc)
        return ""


def run_repo_context(run_uid: str, *, gh: Any = None, git: Any = None) -> dict[str, str] | None:
    """Repo info to hand the agent ({repository, base_branch}), or None when no host.

    Lets the FS/Deploy/QA execution requests tell the worker a repo is connected so
    it triggers the git-pr-workflow skill and delivers via a PR.
    """
    try:
        from agentic_company.platform.delivery.repo_manager import build_run_repo
        from agentic_company.platform.db.runtime_db import _repo_and_run

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return None
        _adapter, spec = built
        return {"repository": spec.repository, "base_branch": spec.base_branch or "main"}
    except Exception:
        return None


def _mirror_pr(run_uid: str, work_item_id: str, pr_url: str, pr_id: str) -> None:
    from agentic_company.platform.db.runtime_db import _submit_pr_mirror

    _submit_pr_mirror(run_uid, work_item_id, pr_url, pr_id)


def _run_dir(run_uid: str) -> Path | None:
    """The run workspace directory for a run uid (where the PR store lives)."""
    try:
        from agentic_company.platform.db.runtime_db import _repo_and_run

        repo, db_run_id = _repo_and_run(run_uid)
        with repo.connect() as conn:
            row = conn.execute(
                "SELECT run_dir FROM runs WHERE id = ?", (db_run_id,)
            ).fetchone()
    except Exception:
        return None
    run_dir = row["run_dir"] if row else None
    return Path(run_dir) if run_dir else None


def record_work_item_pr(
    run_uid: str, work_item_id: str, pr_url: str, pr_number: str, branch: str = ""
) -> None:
    """Persist a work item's PR so downstream agents can review or update it."""
    if not (pr_url and work_item_id):
        return
    run_dir = _run_dir(run_uid)
    if run_dir is None:
        return
    store = run_dir / _PR_STORE_REL
    data: dict[str, Any] = {}
    if store.exists():
        try:
            data = json.loads(store.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    prior = data.get(work_item_id) or {}
    data[work_item_id] = {
        "url": pr_url,
        "number": pr_number,
        "branch": branch,
        "merged": bool(prior.get("merged")),
    }
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_work_item_pr(run_uid: str, work_item_id: str) -> dict[str, Any] | None:
    """The recorded PR for a work item, or None when there is no PR."""
    run_dir = _run_dir(run_uid)
    if run_dir is None:
        return None
    store = run_dir / _PR_STORE_REL
    if not store.exists():
        return None
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = data.get(work_item_id)
    return entry if isinstance(entry, dict) and entry.get("url") else None


def mark_work_item_pr_merged(run_uid: str, work_item_id: str) -> None:
    """Record that the work item's PR was merged, for board/idempotency."""
    pr = get_work_item_pr(run_uid, work_item_id)
    if not pr:
        return
    run_dir = _run_dir(run_uid)
    if run_dir is None:
        return
    store = run_dir / _PR_STORE_REL
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        data[work_item_id]["merged"] = True
        store.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


@dataclass(frozen=True, slots=True)
class PrMergeOutcome:
    """Result of the platform-owned, PR-gated merge after a QA pass."""

    status: str  # merged | already_merged | no_pr | no_repo | unsupported | failed
    pr_url: str = ""
    detail: str = ""


def ensure_recorded_pr_merged(
    run_uid: str, work_item_id: str, *, gh: Any = None, git: Any = None
) -> PrMergeOutcome:
    """Merge the work item's recorded PR after it passes QA — platform-owned.

    The merge runs host-side via the platform's authenticated ``gh``: the sandboxed QA
    worker has no valid GitHub credentials under its ``workspace-write`` policy, so an
    in-worker ``gh pr merge`` 401s. The decision is gated on the PR ARTIFACT the builder
    recorded — a PR exists -> merge it; none exists (e.g. a redeploy that opened no
    branch) -> there is simply nothing to merge, which is never a failure. Guarded:
    a merge problem is surfaced as a status, never raised into delivery.
    """

    try:
        pr = get_work_item_pr(run_uid, work_item_id)
        if not pr:
            return PrMergeOutcome(status="no_pr")
        pr_url = str(pr.get("url") or "")
        if bool(pr.get("merged")):
            return PrMergeOutcome(status="already_merged", pr_url=pr_url)
        from agentic_company.platform.delivery.repo_manager import build_run_repo
        from agentic_company.platform.db.runtime_db import _repo_and_run

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return PrMergeOutcome(status="no_repo", pr_url=pr_url)
        adapter, _spec = built
        if not getattr(adapter.capabilities, "merge", False):
            return PrMergeOutcome(status="unsupported", pr_url=pr_url)
        adapter.merge_pr(pr_url)
        mark_work_item_pr_merged(run_uid, work_item_id)
        LOGGER.info("Merged recorded PR for %s: %s", work_item_id, pr_url)
        return PrMergeOutcome(status="merged", pr_url=pr_url)
    except Exception as exc:  # best-effort: a merge failure never breaks delivery
        LOGGER.warning("Platform PR merge for %s failed: %s", work_item_id, exc)
        return PrMergeOutcome(status="failed", detail=str(exc))
