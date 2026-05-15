"""Codex CLI runner for the autonomous Handoff Agent."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_company.integrations.codex import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts import load_execution_request
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult, ExecutionRequest

LOGGER = logging.getLogger(__name__)

HANDOFF_CODEX_AGENT_ID = "handoff-codex-agent"
HANDOFF_STATUS_PATTERN = re.compile(
    r"^HANDOFF_STATUS:\s*(ready|blocked|failed|unknown)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
HANDOFF_STATUSES = {"ready", "blocked", "failed", "unknown"}
HANDOFF_SUMMARY_MARKDOWN = "09-handoff-summary.md"
HANDOFF_REPORT_HTML = "handoff/release-report.html"
HANDOFF_EVIDENCE_JSON = "handoff/release-evidence.json"

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class HandoffCodexRunner:
    """Run handoff as a Codex-owned specialist execution.

    The platform does not render client copy, choose sections, or summarize
    feature/deployment evidence. The Handoff Codex Agent owns the release package.
    This runner only captures Codex output and validates the minimal contract.
    """

    codex_binary: str | None = None
    sandbox: str = "workspace-write"
    timeout_seconds: int = 1800
    contract_attempts: int = 2
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        event_log = run_dir / "events.jsonl"
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_started",
            {"target_project_dir": request.target_project_dir},
        )

        structured_artifacts: list[str] = []
        summary = ""
        returncode = 1
        contract_errors: list[str] = []
        for attempt in range(1, self.contract_attempts + 1):
            attempt_artifacts = self._run_attempt(
                run_dir,
                request,
                attempt=attempt,
                previous_summary=summary,
                previous_contract_errors=contract_errors,
            )
            summary = attempt_artifacts["summary"]
            returncode = int(attempt_artifacts["returncode"])
            structured_artifacts.extend(attempt_artifacts["artifacts"])
            contract = read_handoff_contract(run_dir, Path(request.target_project_dir), summary)
            if returncode == 0 and contract["contract_valid"]:
                break
            contract_errors = list(contract["contract_errors"])

        contract = read_handoff_contract(run_dir, Path(request.target_project_dir), summary)
        if returncode != 0:
            status = "failed"
            summary = summary or "Handoff Codex exited non-zero."
        elif not contract["contract_valid"]:
            status = "failed"
            summary = _write_contract_failure_artifacts(
                run_dir,
                summary,
                contract["contract_errors"],
            )
        else:
            status = str(contract["status"])

        output_artifacts = _unique_artifacts(
            [
                HANDOFF_SUMMARY_MARKDOWN,
                HANDOFF_REPORT_HTML,
                HANDOFF_EVIDENCE_JSON,
                *structured_artifacts,
            ]
        )
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_completed",
            {"status": status, "artifact": HANDOFF_SUMMARY_MARKDOWN},
        )
        return AgentRunResult(
            agent_id=HANDOFF_CODEX_AGENT_ID,
            status=f"handoff_{status}",
            output_artifacts=output_artifacts,
            summary=summary,
        )

    def _run_attempt(
        self,
        run_dir: Path,
        request: ExecutionRequest,
        *,
        attempt: int,
        previous_summary: str,
        previous_contract_errors: Sequence[str],
    ) -> dict[str, Any]:
        attempt_dir = run_dir / "handoff" / "codex" / f"attempt-{attempt}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        summary_path = attempt_dir / "summary.md"
        prompt_path = attempt_dir / "prompt.md"
        log_path = attempt_dir / "execution.log"
        raw_events_path = attempt_dir / "events.jsonl"
        if raw_events_path.exists():
            raw_events_path.unlink()

        prompt = build_handoff_codex_prompt(
            request,
            run_dir,
            attempt=attempt,
            previous_summary=previous_summary,
            previous_contract_errors=previous_contract_errors,
        )
        command = build_codex_exec_command(
            codex_binary=self.codex_binary,
            model=request.model,
            sandbox=self.sandbox,
            target_project_dir=request.target_project_dir,
            run_dir=run_dir,
            summary_path=summary_path,
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        log_path.write_text(
            f"$ {' '.join(command)}\n"
            f"timeout_seconds={self.timeout_seconds}\n"
            f"agent_id={HANDOFF_CODEX_AGENT_ID}\n"
            f"attempt={attempt}\n\n"
            "Handoff Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir / "events.jsonl",
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_attempt_started",
            {"attempt": attempt},
        )
        try:
            completed = self._execute(command, prompt, log_path, raw_events_path)
        except FileNotFoundError:
            LOGGER.exception("Handoff Codex CLI missing run_id=%s", request.run_id)
            summary_path.write_text(
                "HANDOFF_STATUS: failed\n\nCodex CLI was not found.\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(command, 1, stdout="", stderr="")

        structured_artifacts = write_structured_codex_artifacts(
            run_dir,
            completed.stdout,
            raw_events_filename=raw_events_path.relative_to(run_dir).as_posix(),
        )
        summary = (
            summary_path.read_text(encoding="utf-8")
            if summary_path.exists()
            else completed.stdout.strip()
        )
        write_event(
            run_dir / "events.jsonl",
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_attempt_completed",
            {
                "attempt": attempt,
                "returncode": completed.returncode,
                "summary": summary_path.relative_to(run_dir).as_posix(),
            },
        )
        return {
            "summary": summary,
            "returncode": completed.returncode,
            "artifacts": [
                summary_path.relative_to(run_dir).as_posix(),
                prompt_path.relative_to(run_dir).as_posix(),
                log_path.relative_to(run_dir).as_posix(),
                *structured_artifacts,
            ],
        }

    def _execute(
        self,
        command: Sequence[str],
        prompt: str,
        log_path: Path,
        raw_events_path: Path,
    ) -> subprocess.CompletedProcess[str]:
        if self.command_executor:
            return self.command_executor(
                command,
                prompt,
                self.timeout_seconds,
                log_path,
                raw_events_path,
            )
        return stream_codex_exec_to_log(
            command,
            prompt,
            self.timeout_seconds,
            log_path,
            raw_events_path,
        )


def build_handoff_codex_prompt(
    request: ExecutionRequest,
    run_dir: Path,
    *,
    attempt: int,
    previous_summary: str,
    previous_contract_errors: Sequence[str] | None = None,
) -> str:
    """Build the Handoff Codex Agent prompt without templating the report."""

    target_dir = Path(request.target_project_dir)
    summary_path = run_dir / HANDOFF_SUMMARY_MARKDOWN
    html_path = run_dir / HANDOFF_REPORT_HTML
    evidence_path = run_dir / HANDOFF_EVIDENCE_JSON
    fallback_dir = target_dir / "handoff"
    fallback_summary_path = fallback_dir / HANDOFF_SUMMARY_MARKDOWN
    fallback_html_path = fallback_dir / "release-report.html"
    fallback_evidence_path = fallback_dir / "release-evidence.json"
    repair_note = ""
    if attempt > 1:
        contract_error_lines = "\n".join(f"- {error}" for error in (previous_contract_errors or []))
        repair_note = f"""
Your previous Handoff Codex attempt did not satisfy the output contract.
Complete the package again, then write the required artifacts.

Previous contract errors:
{contract_error_lines or "- Unknown contract error."}

Previous final message:
{previous_summary or "(empty)"}
"""

    return f"""You are the Handoff Codex Agent for agentic-company.

Your agent id is `{HANDOFF_CODEX_AGENT_ID}`.
You are the sole owner of client-facing release communication for this release. The
platform will not render a predefined report template for you.

Generated project directory:
{request.target_project_dir}

Planning run directory:
{run_dir}

Project archetype:
{request.project_archetype}

Release context:
- Feature queue and acceptance criteria are in planning artifacts such as
  `01-intake-brief.json`, `04-workflow-plan.json`, and `05-implementation-brief.md`.
- Fullstack summaries and Codex logs describe what was built.
- QA reports/results describe feature validation.
- Deployment artifacts describe public URLs, cloud resources, risks, and
  post-deploy targets.
- `.delivery-state.json` is the orchestration state of record.

Your job:
- Inspect the planning, implementation, QA, deployment, graph state, and generated
  project evidence.
- Write for this reader persona:
  - A client sponsor, product owner, operations lead, business user, or
    non-technical decision-maker.
  - They are smart, but they do not know Docker, local setup, Azure internals,
    API routes, package files, test harnesses, or source-code layout.
  - They want to know what is ready, where to click, what value it provides,
    how confident we are, what is still limited, and what decision/action is
    expected from them next.
  - Explain technical outcomes in plain business language. For example, say
    "The app was published to a review environment and passed basic live checks"
    instead of naming container apps, registries, ports, revisions, or smoke-test
    commands.
  - Keep the tone professional, calm, direct, and client-ready. Do not sound like an
    internal developer log, CI report, README, or incident ticket.
- Write directly to the client. Use wording like "Your task tracker is ready",
  "Open the app", "What you can do now", and "Recommended next decisions".
  Do not write about the client in the third person.
- Keep the report simple, clear, complete, and non-repetitive. Do not duplicate
  the same status, value statement, limitation, validation result, or next step
  across multiple sections. Prefer one concise section over several overlapping
  cards/tables.
- Do not use internal/meta framing such as "stakeholder review", "business
  review", "review environment", "handoff", "technical evidence", "developer
  artifacts", "engineering follow-up", or "prepared by handoff-codex-agent" in
  the main HTML report.
- Decide what a non-technical client or business user needs to understand
  this release.
- Produce a clear release handoff package that can be shared externally.
- Treat `{html_path}` as the primary client-facing artifact. It must read like a
  business release report, not an engineering handoff, runbook, or developer
  evidence dump.
- Keep the HTML focused on: what was delivered, why it matters, how the client
  can use or try it, current status, business-facing limitations, and recommended
  business next steps.
- Make the release/sprint results concrete and specific. Name the delivered
  capabilities in client language and explain the user-visible behavior, but do
  not expose implementation mechanics.
- Assume the client may only read the HTML report and click one review link.
  The HTML must stand alone without requiring the reader to open technical
  artifacts.
- Do not include local developer setup, Docker Compose commands, package names,
  implementation file paths, raw artifact filenames, container/image names,
  Azure resource names, revision IDs, ports, health endpoint internals, or
  low-level API route details in the main HTML report unless a client explicitly
  needs that detail to review the release.
- If technical details are useful for engineers, place them in the Markdown
  summary and structured evidence JSON, not in the main client HTML.
- Public URLs are client-friendly and should be prominent. Explain them in plain
  language, for example "Open the app here" rather than "Streamlit container app
  on port 8501".
- In the main HTML, show only links a business stakeholder should actually
  click. Usually that means the primary app link. Put technical
  service URLs, integration URLs, API URLs, health URLs, docs URLs, and endpoint
  references into evidence JSON or Markdown unless the release is explicitly a
  technical API handoff for developer stakeholders.
- Do not add sections named "Technical integration", "Technical reviewer",
  "API details", "Developer setup", or similar in the main HTML business
  report.
- Make the HTML report print-friendly so a user can save it as PDF from a browser.
- Make the HTML report display correctly when embedded in Streamlit and when
  opened directly in a browser. Use self-contained CSS with explicit foreground
  and background colors for the page and every major section/card/table.
- Do not rely on transparent backgrounds or inherited Streamlit theme colors.
  Avoid white text on white/light backgrounds, black text on black/dark
  backgrounds, or any low-contrast text. If a section uses white text, that
  exact section must set a dark background. If a section uses a white/light
  background, it must use dark text.
- Prefer a clean light report theme for PDF export: white or near-white page
  background, dark readable body text, bordered cards/tables, and accessible
  link colors. If you add dark header bands, keep all text inside them
  high-contrast and do not leak that text color into light sections.
- Use client-friendly visual hierarchy: executive summary, open/use the app,
  what is included, validation confidence, remaining decisions/risks, and next steps.
  Avoid dense technical tables in the HTML unless they are rewritten for a
  business reader.
- Include useful links, instructions, evidence references, limitations, risks,
  and next steps based on the actual artifacts.
- If public URLs exist, make them prominent and explain what each one is for.
- If deployment or QA is blocked/failed/unknown, say that clearly instead of
  presenting the release as completed.
- If network/search tools are available and genuinely useful, you may use them
  to check public documentation or references. This is optional and
  non-exhaustive; do not depend on network access for local evidence.

Workspace ownership:
- Handoff-owned contract artifacts belong at these exact planning-run paths:
  - `{summary_path}`
  - `{html_path}`
  - `{evidence_path}`
- Handoff-owned helper files, screenshots, transcripts, or source evidence
  belong under `{run_dir}\\handoff`.
- If the sandbox only allows writing inside the generated project, mirror the
  same handoff package under `{fallback_dir}`:
  - `{fallback_summary_path}`
  - `{fallback_html_path}`
  - `{fallback_evidence_path}`
- Do not modify product implementation files.
- Do not write QA or deployment artifacts. Those belong to other agents.
- Do not print or expose secret values.
- Do not recursively list dependency/cache directories such as `.venv`,
  `node_modules`, browser caches, `.pytest_cache`, `dist`, or `build`. Handoff
  should cite relevant reports and evidence, not dump tool caches.
- Do not delete generated project caches or stop runtime processes. Handoff is
  an evidence-packaging role and should not perform cleanup.

Required output contract:
- Write `{summary_path}` as Markdown.
- Write `{html_path}` as a print-friendly HTML report.
- Write `{evidence_path}` as valid structured JSON.
- If those exact paths are blocked by sandbox policy, write equivalent files
  under `{fallback_dir}`. The platform will recover those fallback artifacts.
- End your final message with exactly one status line:
  `HANDOFF_STATUS: ready`, `HANDOFF_STATUS: blocked`,
  `HANDOFF_STATUS: failed`, or `HANDOFF_STATUS: unknown`.

The evidence JSON must be valid JSON and include at least:
```json
{{
  "status": "ready",
  "project": {{
    "name": "",
    "goal": ""
  }},
  "release_summary": "",
  "delivered_features": [],
  "public_urls": [],
  "qa_summary": {{
    "status": "",
    "evidence": []
  }},
  "deployment_summary": {{
    "status": "",
    "evidence": []
  }},
  "known_limitations": [],
  "recommended_next_steps": [],
  "artifact_references": []
}}
```

If the handoff is ready, the Markdown and HTML should be understandable by a
non-engineering stakeholder. If blocked, failed, or unknown, explain exactly
which evidence prevents client-ready handoff and what should happen next.
{repair_note}
"""


def read_handoff_contract(run_dir: Path, target_dir: Path, summary: str) -> dict[str, Any]:
    _recover_misplaced_handoff_contract_artifacts(run_dir, target_dir)
    status = _parse_status(summary)
    errors: list[str] = []
    if status is None:
        errors.append(
            "Handoff Codex final message did not include "
            "HANDOFF_STATUS: ready|blocked|failed|unknown."
        )

    for relative_path in [
        HANDOFF_SUMMARY_MARKDOWN,
        HANDOFF_REPORT_HTML,
        HANDOFF_EVIDENCE_JSON,
    ]:
        if not (run_dir / relative_path).exists():
            errors.append(f"Missing required handoff artifact: {relative_path}.")

    payload, payload_errors = _load_json_object(run_dir / HANDOFF_EVIDENCE_JSON)
    errors.extend(payload_errors)
    if payload:
        result_status = str(payload.get("status", "")).lower()
        if result_status not in HANDOFF_STATUSES:
            errors.append("Handoff evidence JSON must include ready|blocked|failed|unknown.")
        elif status and result_status != status:
            errors.append("Handoff evidence JSON status does not match final status line.")
    if status == "ready":
        errors.extend(_client_html_contract_errors(run_dir / HANDOFF_REPORT_HTML))

    return {
        "status": status or str(payload.get("status") or "unknown").lower(),
        "contract_valid": not errors,
        "contract_errors": errors,
    }


def _recover_misplaced_handoff_contract_artifacts(run_dir: Path, target_dir: Path) -> None:
    for source, destination in [
        (target_dir / "handoff" / HANDOFF_SUMMARY_MARKDOWN, run_dir / HANDOFF_SUMMARY_MARKDOWN),
        (target_dir / "handoff" / "release-report.html", run_dir / HANDOFF_REPORT_HTML),
        (target_dir / "handoff" / "release-evidence.json", run_dir / HANDOFF_EVIDENCE_JSON),
        (target_dir / HANDOFF_SUMMARY_MARKDOWN, run_dir / HANDOFF_SUMMARY_MARKDOWN),
        (target_dir / HANDOFF_REPORT_HTML, run_dir / HANDOFF_REPORT_HTML),
        (target_dir / HANDOFF_EVIDENCE_JSON, run_dir / HANDOFF_EVIDENCE_JSON),
    ]:
        if not source.exists():
            continue
        if destination.exists() and source.stat().st_mtime <= destination.stat().st_mtime:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _parse_status(summary: str) -> str | None:
    match = HANDOFF_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _load_json_object(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, [f"Missing required handoff evidence JSON: {HANDOFF_EVIDENCE_JSON}."]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {}, [f"Handoff evidence JSON is invalid: {exc}."]
    if not isinstance(loaded, dict):
        return {}, ["Handoff evidence JSON must be an object."]
    return loaded, []


def _client_html_contract_errors(path: Path) -> list[str]:
    if not path.exists():
        return []
    html = path.read_text(encoding="utf-8", errors="ignore").lower()
    developer_terms = {
        "docker compose": "Docker Compose/local developer commands",
        "localhost": "localhost developer URLs",
        "resource group": "cloud resource names",
        "container registry": "cloud registry internals",
        "revision": "deployment revision internals",
        "generated-project": "generated project file paths",
        ".delivery-state": "orchestration artifact names",
        ".json": "raw JSON artifact names",
        ".md": "raw Markdown artifact names",
        "technical integration": "technical integration links",
        "stakeholder": "third-person stakeholder framing",
        "business review": "internal review framing",
        "review environment": "internal review-environment framing",
        "dev review": "internal dev-environment framing",
        "technical reviewer": "technical reviewer content",
        "developer setup": "developer setup content",
        "handoff-codex-agent": "internal agent identity",
        "technical references": "technical evidence references",
        "evidence file": "technical evidence references",
        "engineering follow-up": "engineering follow-up content",
        "developer artifacts": "developer artifact references",
        "api details": "API implementation details",
        "pyproject": "package metadata",
        "port 8501": "container port details",
        "port 8000": "container port details",
    }
    return [
        f"Client HTML contains developer-facing detail: {description}."
        for term, description in developer_terms.items()
        if term in html
    ]


def _write_contract_failure_artifacts(
    run_dir: Path,
    summary: str,
    errors: list[str],
) -> str:
    handoff_dir = run_dir / "handoff"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "failed",
        "project": {"name": "", "goal": ""},
        "release_summary": "Handoff Codex did not satisfy the output contract.",
        "delivered_features": [],
        "public_urls": [],
        "qa_summary": {"status": "", "evidence": []},
        "deployment_summary": {"status": "", "evidence": []},
        "known_limitations": ["Handoff package contract was incomplete."],
        "recommended_next_steps": ["Review Handoff Codex contract errors and rerun handoff."],
        "artifact_references": [],
        "contract_errors": errors,
    }
    (run_dir / HANDOFF_EVIDENCE_JSON).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = "\n".join(
        [
            "# Release Handoff Summary",
            "",
            "Status: failed",
            "",
            "The Handoff Codex Agent did not satisfy the required output contract.",
            "",
            "## Contract Errors",
            "",
            *[f"- {error}" for error in errors],
            "",
            "## Last Handoff Codex Message",
            "",
            "```text",
            summary or "(empty)",
            "```",
            "",
        ]
    )
    (run_dir / HANDOFF_SUMMARY_MARKDOWN).write_text(report, encoding="utf-8")
    (run_dir / HANDOFF_REPORT_HTML).write_text(
        "<!doctype html><html><body><h1>Handoff Failed</h1>"
        "<p>The Handoff Codex Agent did not satisfy the output contract.</p></body></html>\n",
        encoding="utf-8",
    )
    return f"{report}\nHANDOFF_STATUS: failed\n"


def _unique_artifacts(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
