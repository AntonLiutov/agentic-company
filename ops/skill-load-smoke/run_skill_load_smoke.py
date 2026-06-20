"""Live proof that a Codex worker loads its skill via NATIVE discovery.

Runs a REAL `codex exec` through the exact production chokepoint
(``stream_codex_exec_to_log``, which provisions the catalog into Codex's native
``.agents/skills`` discovery path — no hand-injected index) and asks the model a
question whose correct answer lives ONLY inside a skill's SKILL.md. Codex auto-
discovers the skill and triggers it by its ``description``. Then it checks two
things from the run artifacts:

  1. did the worker OPEN the skill file?  (the raw codex events reference the
     provisioned ``.agents/skills/<id>/SKILL.md`` path)  -> skill loaded on demand
  2. does the final answer reflect the skill's authoritative guidance?
     (e.g. "Playwright is pre-installed, do not install" for QA, or the
     "Quiet Console / premium" palette for the builder)  -> proves it applied

Usage (uses your logged-in Codex/ChatGPT auth; this DOES call the model):

    uv run python ops/skill-load-smoke/run_skill_load_smoke.py qa-agent
    uv run python ops/skill-load-smoke/run_skill_load_smoke.py fullstack-agent
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.integrations.codex.runner import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
)

# A question per agent whose right answer is ONLY in that agent's skill, plus the
# markers that prove the skill's guidance reached the answer.
PROBES: dict[str, dict[str, object]] = {
    "qa-agent": {
        "question": (
            "You are doing browser QA for a generated web app on THIS Windows host. "
            "In ~120 words, state exactly how you set up and run Playwright to capture "
            "screenshots: do you install Playwright/Chromium or not, and what launch "
            "flags do you use? Answer in plain text only. Do NOT create, modify, or "
            "delete any files."
        ),
        "answer_markers": [
            "pre-install",
            "preinstall",
            "already install",
            "do not install",
            "don't install",
            "playwright_browsers_path",
            "--disable-gpu",
        ],
    },
    "fullstack-agent": {
        "question": (
            "You are about to build a web app UI for a generated product. In ~120 "
            "words, describe the exact visual style, color palette, and design "
            "principles you would apply. Answer in plain text only. Do NOT create, "
            "modify, or delete any files."
        ),
        "answer_markers": [
            "quiet console",
            "premium",
            "linear",
            "vercel",
            "palette",
            "intentional",
        ],
    },
}


def main() -> int:
    agent_id = sys.argv[1] if len(sys.argv) > 1 else "qa-agent"
    probe = PROBES.get(agent_id)
    if probe is None:
        print(f"no probe for {agent_id}; choose one of {list(PROBES)}")
        return 2

    work = Path(tempfile.mkdtemp(prefix=f"skill-smoke-{agent_id}-"))
    target = work / "project"
    target.mkdir(parents=True, exist_ok=True)
    summary = work / "summary.md"
    log = work / "exec.log"
    raw = work / "raw-events.jsonl"

    command = build_codex_exec_command(
        codex_binary=None,
        model=DEFAULT_CODEX_MODEL,
        sandbox="read-only",  # the model may read files (incl. the skill) but write none
        target_project_dir=str(target),
        run_dir=work,
        summary_path=summary,
        force_sandbox=True,
    )

    print(f"[smoke] agent={agent_id} model={DEFAULT_CODEX_MODEL}")
    print(f"[smoke] workspace={work}")
    print("[smoke] calling real Codex (this uses your Codex auth and costs tokens)...")
    # stream_codex_exec_to_log provisions the catalog into Codex's native
    # .agents/skills path (cwd-parent), so the worker auto-discovers the skill.
    stream_codex_exec_to_log(
        command,
        str(probe["question"]),
        300,
        log,
        raw,
        trace_agent_id=agent_id,
        trace_run_dir=work,
    )

    answer = summary.read_text(encoding="utf-8").strip() if summary.exists() else ""
    raw_text = raw.read_text(encoding="utf-8", errors="ignore") if raw.exists() else ""

    # native discovery serves the skill from the provisioned .agents/skills path
    opened_skill = ".agents" in raw_text and "SKILL.md" in raw_text
    markers = [m for m in probe["answer_markers"] if m in answer.lower()]
    applied = bool(markers)

    print("\n================ CODEX ANSWER ================")
    print(answer or "(no final message captured)")
    print("==============================================\n")
    print(f"[result] opened the SKILL.md on demand : {opened_skill}")
    print(f"[result] answer reflects the skill      : {applied}  (markers: {markers})")
    verdict = "PASS" if (opened_skill or applied) else "FAIL"
    print(f"[result] VERDICT: {verdict}")
    print(f"[result] raw events: {raw}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
