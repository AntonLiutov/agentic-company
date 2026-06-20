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
# workspace so the QA stage (a later Codex worker) can learn the PR exists and the
# platform can merge/comment on the QA verdict.
_PR_STORE_REL = "delivery/work-item-prs.json"

# Only the agents that produce committable code/config open PRs.
_PR_OWNER_AGENTS = {"fullstack-agent", "deployment-agent"}
_REPO_ENSURED: set[str] = set()  # run_uids whose working tree is already prepared


@dataclass(frozen=True, slots=True)
class WorkItemPrMergeResult:
    """Outcome of the platform-owned post-QA PR merge guard."""

    ok: bool
    status: str
    message: str
    pr_url: str = ""


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
    """Persist a work item's PR so QA can review it and the platform can merge it."""
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
    """Record that the agent (QA) merged the work item's PR, for board/idempotency."""
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


def merge_work_item_pr_after_qa_pass(
    run_uid: str, work_item_id: str, *, gh: Any = None, git: Any = None
) -> WorkItemPrMergeResult:
    """Merge the recorded work-item PR after QA passes.

    This is the deterministic backstop for the model-facing git-pr-workflow
    instruction: QA may still review the PR itself, but the platform must not mark
    a repo-backed work item complete while the PR remains unmerged.
    """

    try:
        from agentic_company.platform.delivery.repo_manager import build_run_repo
        from agentic_company.platform.db.runtime_db import _repo_and_run

        repo, db_run_id = _repo_and_run(run_uid)
        built = build_run_repo(repo, db_run_id, gh=gh, git=git)
        if built is None:
            return WorkItemPrMergeResult(
                ok=True,
                status="skipped",
                message="No repository host is connected; no PR merge is required.",
            )
        adapter, _spec = built
        pr = get_work_item_pr(run_uid, work_item_id)
        if not pr:
            return WorkItemPrMergeResult(
                ok=False,
                status="missing_pr",
                message=(
                    f"Repository is connected, but no recorded PR exists for work item "
                    f"{work_item_id}."
                ),
            )
        pr_url = str(pr.get("url") or "")
        if bool(pr.get("merged")):
            return WorkItemPrMergeResult(
                ok=True,
                status="already_merged",
                message=f"PR already recorded as merged for work item {work_item_id}.",
                pr_url=pr_url,
            )
        if not getattr(adapter.capabilities, "merge", False):
            return WorkItemPrMergeResult(
                ok=False,
                status="unsupported",
                message=f"Repository adapter does not support PR merge for {pr_url}.",
                pr_url=pr_url,
            )
        adapter.merge_pr(pr_url)
        mark_work_item_pr_merged(run_uid, work_item_id)
        return WorkItemPrMergeResult(
            ok=True,
            status="merged",
            message=f"Merged PR for work item {work_item_id}: {pr_url}",
            pr_url=pr_url,
        )
    except Exception as exc:
        LOGGER.warning("PR merge after QA pass failed for %s: %s", work_item_id, exc)
        return WorkItemPrMergeResult(
            ok=False,
            status="failed",
            message=f"PR merge after QA pass failed for work item {work_item_id}: {exc}",
        )
