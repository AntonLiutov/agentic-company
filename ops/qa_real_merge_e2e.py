"""REAL end-to-end: does a live QA worker REVIEW and actually MERGE a real PR?

Not intent — action. Opens a throwaway PR on the test repo, runs a REAL Codex QA
worker (cwd = the cloned repo, sandbox = workspace-write, exactly like a real run)
with the git-pr-workflow skill provisioned natively, and tells it: review work item
QATEST and MERGE it on a pass. Then checks the truth from GitHub:

  - did the worker actually execute `gh pr merge`?  (scan its raw events)
  - is the PR MERGED on GitHub now?                 (gh pr view)

Cleans up afterward (closes the PR + deletes the branch if it was not merged).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.integrations.codex.runner import (
    _NATIVE_SKILLS_READY,
    build_codex_exec_command,
    stream_codex_exec_to_log,
)

REPO = "AntonLiutov/adl-phase2-smoke"


def sh(args: list[str], cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {r.returncode}: {r.stderr.strip()}")
    return r.stdout.strip()


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="qa-real-merge-"))
    proj = work / "generated-project"
    branch = f"adl/qatest-{os.getpid()}"
    pr_num = ""
    try:
        print(f"[e2e] cloning {REPO} ...")
        sh(["gh", "repo", "clone", REPO, str(proj), "--", "--depth", "1"])
        sh(["git", "switch", "-c", branch], cwd=str(proj))
        (proj / "QA-MERGE-TEST.md").write_text(
            f"Throwaway QA-merge e2e marker ({os.getpid()}). Safe to merge or delete.\n",
            encoding="utf-8",
        )
        sh(["git", "add", "-A"], cwd=str(proj))
        sh(
            [
                "git",
                "-c",
                "user.email=qa-e2e@adl.local",
                "-c",
                "user.name=ADL QA E2E",
                "commit",
                "-m",
                "test(qatest): throwaway change for QA-merge e2e",
            ],
            cwd=str(proj),
        )
        sh(["git", "push", "-u", "origin", branch], cwd=str(proj))
        pr_url = sh(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                REPO,
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                "[QATEST] QA-merge e2e (throwaway)",
                "--body",
                "Throwaway PR to verify the QA worker reviews AND merges. Safe to merge/close.",
            ]
        )
        pr_num = pr_url.rstrip("/").split("/")[-1]
        print(f"[e2e] opened PR #{pr_num}: {pr_url}")

        _NATIVE_SKILLS_READY.clear()
        summary = work / "summary.md"
        log = work / "exec.log"
        raw = work / "raw.jsonl"
        sandbox = sys.argv[1] if len(sys.argv) > 1 else "workspace-write"
        print(f"[e2e] sandbox policy = {sandbox}")
        cmd = build_codex_exec_command(
            codex_binary=None,
            model=DEFAULT_CODEX_MODEL,
            sandbox=sandbox,
            target_project_dir=str(proj),
            run_dir=work,
            summary_path=summary,
            force_sandbox=True,
        )
        prompt = (
            "You are the Quality Reviewer (QA) for work item QATEST. The connected repository "
            f"is {REPO} (base main). The builder opened pull request #{pr_num} on branch "
            f"{branch}. Follow your git-pr-workflow skill: inspect git/the PR FIRST, then review. "
            "This is a trivial doc-only change (adds one marker file) and is acceptable, so it "
            f"PASSES. On a pass you MUST merge it with `gh pr merge {pr_num} --squash "
            "--delete-branch`. Do it now; report that you merged."
        )
        print("[e2e] running REAL QA worker (cwd=repo, workspace-write) ...")
        stream_codex_exec_to_log(
            cmd, prompt, 480, log, raw, trace_agent_id="qa-codex-agent", trace_run_dir=work
        )

        raw_text = raw.read_text(encoding="utf-8", errors="ignore") if raw.exists() else ""
        ran_merge = "pr merge" in raw_text.lower()
        state_json = sh(
            ["gh", "pr", "view", pr_num, "--repo", REPO, "--json", "state,mergedAt,mergedBy"],
            check=False,
        )
        state = json.loads(state_json) if state_json else {}
        answer = summary.read_text(encoding="utf-8").strip() if summary.exists() else ""

        print("\n================ TRUTH ================")
        print("worker executed a `gh pr merge`:", ran_merge)
        print("PR state on GitHub           :", state.get("state"))
        print("merged at                    :", state.get("mergedAt"))
        print("=======================================")
        print("\n--- QA worker summary (first 1000 chars) ---")
        print(answer[:1000])
        return 0
    finally:
        # cleanup: if the worker did NOT merge, close the throwaway PR + delete the branch
        if pr_num:
            st = sh(["gh", "pr", "view", pr_num, "--repo", REPO, "--json", "state"], check=False)
            if not st or json.loads(st).get("state") != "MERGED":
                sh(["gh", "pr", "close", pr_num, "--repo", REPO, "--delete-branch"], check=False)
                print(f"[cleanup] closed throwaway PR #{pr_num} + deleted {branch}")


if __name__ == "__main__":
    raise SystemExit(main())
