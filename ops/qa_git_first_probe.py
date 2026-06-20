"""Live probe: does the QA worker inspect git FIRST?

Splices a candidate "Reviewer workflow (QA)" section into the real git-pr-workflow
SKILL.md, provisions the catalog into a temp run workspace, runs a REAL Codex QA
worker (cwd = run_dir, sandbox = workspace-write — the exact conditions of a real
run), and asks it to list its QA procedure IN EXECUTION ORDER. Then it checks whether
the worker's FIRST step is a git/PR inspection (git remote/status/branch, gh pr ...)
rather than serving/browser/testing.

Emits one JSON line: {"git_first": bool, "first_step": "...", "answer": "..."}.

Usage:
    python ops/qa_git_first_probe.py current            # baseline (real skill as-is)
    python ops/qa_git_first_probe.py path/to/qa-section.md [variant]
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.integrations.codex.runner import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
    _NATIVE_SKILLS_READY,
)
from agentic_company.platform.skills import provision_native_skills, SKILL_CATALOG_DIR

_REAL_SKILL = SKILL_CATALOG_DIR / "git-pr-workflow" / "SKILL.md"
_QA_HEADER = "## Reviewer workflow (QA)"

_VARIANTS = {
    "f1": "work item F1 on branch adl/f1; the builder opened a pull request for it",
    "f2": "work item F2 on branch adl/f2; a pull request already exists (URL not given to you)",
    "nourl": "work item F1; a repository is connected but you were NOT told the PR URL",
}

# A step counts as git-first only if it inspects the repo/PR BEFORE any app exercise.
_GIT_SIGNALS = ("git remote", "git status", "git branch", "git log", "gh pr", "git fetch", "pull request")
_APP_SIGNALS = ("serve", "browser", "playwright", "npm", "open the app", "localhost", "screenshot", "http://")


def _spliced_skill(section_file: str) -> str:
    real = _REAL_SKILL.read_text(encoding="utf-8")
    if section_file == "current":
        return real
    candidate = Path(section_file).read_text(encoding="utf-8").strip()
    head, sep, tail = real.partition(_QA_HEADER)
    if not sep:
        return real + "\n\n" + candidate
    # replace from the QA header up to the next top-level "## " section
    after = tail.split("\n## ", 1)
    rest = ("\n## " + after[1]) if len(after) > 1 else ""
    return head + candidate.rstrip() + "\n" + rest


def _first_step(answer: str) -> str:
    for line in answer.splitlines():
        if re.match(r"\s*(1[.)]|step\s*1\b|first\b|-)\s", line.strip(), re.IGNORECASE):
            return line.strip()
    return answer.strip().splitlines()[0] if answer.strip() else ""


def _is_git_first(answer: str) -> bool:
    low = answer.lower()
    git_at = min((low.find(s) for s in _GIT_SIGNALS if s in low), default=-1)
    app_at = min((low.find(s) for s in _APP_SIGNALS if s in low), default=-1)
    if git_at < 0:
        return False
    return app_at < 0 or git_at < app_at  # git mentioned, and before any app exercise


def main() -> int:
    section_file = sys.argv[1] if len(sys.argv) > 1 else "current"
    variant = sys.argv[2] if len(sys.argv) > 2 else "f1"
    context = _VARIANTS.get(variant, _VARIANTS["f1"])

    _NATIVE_SKILLS_READY.clear()
    run_dir = Path(tempfile.mkdtemp(prefix="qa-gitfirst-"))
    provision_native_skills(run_dir)  # full catalog into $CWD/.agents/skills (cwd = run_dir)
    (run_dir / ".agents" / "skills" / "git-pr-workflow" / "SKILL.md").write_text(
        _spliced_skill(section_file), encoding="utf-8"
    )

    summary = run_dir / "summary.md"
    log = run_dir / "exec.log"
    raw = run_dir / "raw.jsonl"
    cmd = build_codex_exec_command(
        codex_binary=None, model=DEFAULT_CODEX_MODEL, sandbox="workspace-write",
        target_project_dir=str(run_dir), run_dir=run_dir, summary_path=summary, force_sandbox=True,
    )
    prompt = (
        "You are the Quality Reviewer (QA). The run has a connected repository "
        f"(repository acme/app, base main); you are reviewing {context}. "
        "Do NOT modify or create any files, and do NOT run any commands now. "
        "Output ONLY a numbered list of your QA steps IN THE EXACT ORDER you perform them. "
        "Step 1 must be your literal FIRST action. Be concrete (name the commands)."
    )
    stream_codex_exec_to_log(
        cmd, prompt, 240, log, raw, trace_agent_id="qa-codex-agent", trace_run_dir=run_dir
    )
    answer = summary.read_text(encoding="utf-8").strip() if summary.exists() else ""
    verdict = {
        "variant": variant,
        "skill": section_file,
        "git_first": _is_git_first(answer),
        "first_step": _first_step(answer),
        "answer": answer,
    }
    print("PROBE_JSON " + json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
