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
    DEFAULT_CODEX_SANDBOX,
    build_codex_exec_command,
    stream_codex_exec_to_log,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts import load_execution_request, read_text_artifact
from agentic_company.platform.events import write_event
from agentic_company.platform.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)
from agentic_company.platform.messages import (
    AgentMessageStore,
    render_incoming_messages_for_prompt,
)
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
SPRINT_SCOPE_PATTERN = re.compile(r"\bsprint[-_\s]?\d+\b", re.IGNORECASE)

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True, slots=True)
class HandoffContractPaths:
    """Canonical handoff artifact paths for one handoff scope."""

    summary: str
    html: str
    evidence: str


@dataclass(slots=True)
class HandoffCodexRunner:
    """Run handoff as a Codex-owned specialist execution.

    The platform does not render client copy, choose sections, or summarize
    feature/deployment evidence. The Handoff Codex Agent owns the release package.
    This runner only captures Codex output and validates the minimal contract.
    """

    codex_binary: str | None = None
    sandbox: str = DEFAULT_CODEX_SANDBOX
    timeout_seconds: int = 1800
    contract_attempts: int = 1
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        contract_paths = handoff_contract_paths(request, run_dir)
        event_log = run_dir / "events.jsonl"
        execution_id = _execution_id(request)
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_started",
            {"target_project_dir": request.target_project_dir, "execution_id": execution_id},
        )

        structured_artifacts: list[str] = []
        summary = ""
        returncode = 1
        contract_errors: list[str] = []
        codex_thread_id = ""
        for attempt in range(1, self.contract_attempts + 1):
            attempt_artifacts = self._run_attempt(
                run_dir,
                request,
                attempt=attempt,
                execution_id=execution_id,
                previous_summary=summary,
                previous_contract_errors=contract_errors,
            )
            summary = attempt_artifacts["summary"]
            returncode = int(attempt_artifacts["returncode"])
            codex_thread_id = str(attempt_artifacts.get("codex_thread_id") or codex_thread_id)
            structured_artifacts.extend(attempt_artifacts["artifacts"])
            contract = read_handoff_contract(
                run_dir,
                Path(request.target_project_dir),
                summary,
                paths=contract_paths,
            )
            if returncode == 0 and contract["contract_valid"]:
                break
            contract_errors = list(contract["contract_errors"])

        contract = read_handoff_contract(
            run_dir,
            Path(request.target_project_dir),
            summary,
            paths=contract_paths,
        )
        if returncode != 0:
            status = "failed"
            summary = summary or "Handoff Codex exited non-zero."
        elif not contract["contract_valid"]:
            status = "failed"
            summary = _write_contract_failure_artifacts(
                run_dir,
                summary,
                contract["contract_errors"],
                paths=contract_paths,
            )
        else:
            status = str(contract["status"])

        output_artifacts = _unique_artifacts(
            [
                contract_paths.summary,
                contract_paths.html,
                contract_paths.evidence,
                *structured_artifacts,
            ]
        )
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_completed",
            {
                "status": status,
                "artifact": contract_paths.summary,
                "execution_id": execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=HANDOFF_CODEX_AGENT_ID,
            status=f"handoff_{status}",
            output_artifacts=output_artifacts,
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=[] if status == "ready" else [summary.strip()[:500]],
            recommended_next_action=(
                "Proceed to Team Lead handoff review."
                if status == "ready"
                else "Revise handoff package using returned findings."
            ),
        )

    def _run_attempt(
        self,
        run_dir: Path,
        request: ExecutionRequest,
        *,
        attempt: int,
        execution_id: str,
        previous_summary: str,
        previous_contract_errors: Sequence[str],
    ) -> dict[str, Any]:
        attempt_dir = execution_artifact_dir(
            root=run_dir / "handoff" / "codex",
            execution_id=execution_id,
            attempt=attempt,
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=HANDOFF_CODEX_AGENT_ID,
            attempt=attempt,
        )
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
            resume_session_id=request.codex_resume_thread_id,
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        log_path.write_text(
            f"$ {' '.join(command)}\n"
            f"timeout_seconds={self.timeout_seconds}\n"
            f"agent_id={HANDOFF_CODEX_AGENT_ID}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n"
            f"attempt={attempt}\n\n"
            "Handoff Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir / "events.jsonl",
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_attempt_started",
            {
                "attempt": attempt,
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
            },
        )
        try:
            completed = self._execute(
                command,
                prompt,
                log_path,
                raw_events_path,
                codex_execution_id=codex_execution_id,
            )
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
            read_text_artifact(summary_path) if summary_path.exists() else completed.stdout.strip()
        )
        codex_thread_id = extract_codex_thread_id(raw_events_path) or request.codex_resume_thread_id
        write_event(
            run_dir / "events.jsonl",
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_attempt_completed",
            {
                "attempt": attempt,
                "returncode": completed.returncode,
                "summary": summary_path.relative_to(run_dir).as_posix(),
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return {
            "summary": summary,
            "returncode": completed.returncode,
            "codex_thread_id": codex_thread_id,
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
        *,
        codex_execution_id: str,
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
            codex_execution_id=codex_execution_id,
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
    contract_paths = handoff_contract_paths(request, run_dir)
    summary_path = run_dir / contract_paths.summary
    html_path = run_dir / contract_paths.html
    evidence_path = run_dir / contract_paths.evidence
    fallback_summary_path = target_dir / contract_paths.summary
    fallback_html_path = target_dir / contract_paths.html
    fallback_evidence_path = target_dir / contract_paths.evidence
    upstream_messages = render_incoming_messages_for_prompt(
        run_dir,
        to_agent="documentation-handoff-agent",
    )
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

Platform execution id:
{request.execution_id or "(not provided)"}

Execution intent:
{request.execution_intent or "(not provided)"}

Release context:
- Feature queue and acceptance criteria are in upstream planning artifacts and
  the current delivery execution request.
- Fullstack summaries and Codex logs describe what was built.
- QA reports/results describe feature validation.
- Deployment artifacts describe public URLs, cloud resources, risks, and
  post-deploy targets.
- `.delivery-state.json` is the orchestration state of record.

Upstream agent messages:
{upstream_messages}

Your job:
- Inspect the current sprint/request, upstream planning artifacts, delivery
  state, downstream agent messages, implementation summaries, QA evidence, and
  deployment evidence when it exists.
- Decide what the recipient of the handoff needs to know from the actual
  artifacts. Do not follow a fixed section template when the sprint context
  calls for something simpler.
- Write a clear handoff package:
  - a Markdown summary for internal/team reading;
  - a client-facing HTML report for the delivered sprint/release;
  - a structured JSON evidence manifest for downstream automation.
- In the HTML, explain what was delivered, what was validated, what is limited,
  how to try or review it when a usable URL/instruction exists, and what decision
  or next step is expected.
- Keep technical details proportional to the audience and the request. If this
  is a business handoff, translate implementation details into business impact.
  If this is a technical/API handoff, include enough technical detail to be
  useful.
- Do not overclaim. If deployment, QA, public URLs, security, persistence, or
  production readiness are absent/blocked/not in scope, say that plainly.
- Keep the HTML readable, self-contained, and usable in a browser or Streamlit
  preview.
- You may use network/search tools only when they help explain external
  references; local run artifacts are the source of truth.

Workspace ownership:
- Treat `{run_dir}` as the delivery run workspace and
  `{request.target_project_dir}` as the generated product project.
- You may use network-backed tools for evidence lookup or documentation checks
  when they help explain the release, but keep authored artifacts inside the
  run workspace.
- Do not modify files outside `{run_dir}`. In particular, do not modify the
  platform repository source, root configuration, user home files, or unrelated
  projects.
- Handoff-owned contract artifacts belong at these exact planning-run paths:
  - `{summary_path}`
  - `{html_path}`
  - `{evidence_path}`
- Handoff-owned helper files, screenshots, transcripts, or source evidence
  belong under `{run_dir}\\handoff`.
- If the sandbox only allows writing inside the generated project, mirror the
  same handoff package under the generated project at these exact paths:
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

Minimal output contract:
- Write `{summary_path}` as Markdown.
- Write `{html_path}` as a print-friendly HTML report.
- Write `{evidence_path}` as valid structured JSON.
- If those exact planning-run paths are blocked by sandbox policy, write
  equivalent files under the generated project at the fallback paths listed
  above. The platform will recover those fallback artifacts.
- End your final message with a short status when useful, for example
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
non-engineering stakeholder. If this is a sprint handoff, keep it scoped to that
sprint. If this is a project/final handoff, consolidate the completed sprint
handoffs and project-level evidence. If blocked, failed, or unknown, explain
exactly which evidence prevents client-ready handoff and what should happen
next.
{repair_note}
"""


def handoff_contract_paths(request: ExecutionRequest, run_dir: Path) -> HandoffContractPaths:
    """Return scoped handoff artifact paths for the current Handoff request."""

    message = (
        AgentMessageStore(run_dir).get(request.parent_message_id)
        if request.parent_message_id
        else None
    )
    explicit_scope_text = " ".join(
        part
        for part in [
            request.execution_intent,
            request.active_feature.get("id") if request.active_feature else "",
            message.correlation_id if message else "",
        ]
        if part
    )
    if SPRINT_SCOPE_PATTERN.search(explicit_scope_text):
        sprint_id = _handoff_sprint_id(explicit_scope_text, request)
        return HandoffContractPaths(
            summary=f"handoff/sprints/{sprint_id}/09-handoff-summary.md",
            html=f"handoff/sprints/{sprint_id}/release-report.html",
            evidence=f"handoff/sprints/{sprint_id}/release-evidence.json",
        )

    normalized = explicit_scope_text.lower()
    if any(token in normalized for token in ("project handoff", "final handoff", "whole project")):
        return HandoffContractPaths(
            summary="handoff/project/09-handoff-summary.md",
            html="handoff/project/release-report.html",
            evidence="handoff/project/release-evidence.json",
        )

    sprint_id = _handoff_sprint_id(explicit_scope_text, request)
    return HandoffContractPaths(
        summary=f"handoff/sprints/{sprint_id}/09-handoff-summary.md",
        html=f"handoff/sprints/{sprint_id}/release-report.html",
        evidence=f"handoff/sprints/{sprint_id}/release-evidence.json",
    )


def read_handoff_contract(
    run_dir: Path,
    target_dir: Path,
    summary: str,
    *,
    paths: HandoffContractPaths | None = None,
) -> dict[str, Any]:
    contract_paths = paths or HandoffContractPaths(
        summary=HANDOFF_SUMMARY_MARKDOWN,
        html=HANDOFF_REPORT_HTML,
        evidence=HANDOFF_EVIDENCE_JSON,
    )
    _recover_misplaced_handoff_contract_artifacts(run_dir, target_dir, paths=contract_paths)
    errors: list[str] = []

    for relative_path in [contract_paths.summary, contract_paths.html, contract_paths.evidence]:
        if not (run_dir / relative_path).exists():
            errors.append(f"Missing required handoff artifact: {relative_path}.")

    payload, payload_errors = _load_json_object(run_dir / contract_paths.evidence)
    errors.extend(payload_errors)
    status = _parse_status(summary)
    if payload:
        result_status = str(payload.get("status", "")).lower()
        if result_status in HANDOFF_STATUSES:
            status = status or result_status
        elif result_status:
            errors.append("Handoff evidence JSON must include ready|blocked|failed|unknown.")
    if status is None and not errors:
        status = "ready"

    return {
        "status": status or "failed",
        "contract_valid": not errors,
        "contract_errors": errors,
    }


def _recover_misplaced_handoff_contract_artifacts(
    run_dir: Path,
    target_dir: Path,
    *,
    paths: HandoffContractPaths,
) -> None:
    for source, destination in _handoff_recovery_candidates(target_dir, run_dir, paths):
        if not source.exists():
            continue
        if destination.exists() and source.stat().st_mtime <= destination.stat().st_mtime:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _parse_status(summary: str) -> str | None:
    match = HANDOFF_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _execution_id(request: ExecutionRequest) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=HANDOFF_CODEX_AGENT_ID,
        target=request.active_feature.get("id") if request.active_feature else "sprint",
        intent=request.execution_intent or "handoff",
    )


def _load_json_object(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, [f"Missing required handoff evidence JSON: {path.as_posix()}."]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {}, [f"Handoff evidence JSON is invalid: {exc}."]
    if not isinstance(loaded, dict):
        return {}, ["Handoff evidence JSON must be an object."]
    return loaded, []


def _write_contract_failure_artifacts(
    run_dir: Path,
    summary: str,
    errors: list[str],
    *,
    paths: HandoffContractPaths,
) -> str:
    for relative_path in (paths.summary, paths.html, paths.evidence):
        (run_dir / relative_path).parent.mkdir(parents=True, exist_ok=True)
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
    (run_dir / paths.evidence).write_text(
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
    (run_dir / paths.summary).write_text(report, encoding="utf-8")
    (run_dir / paths.html).write_text(
        "<!doctype html><html><body><h1>Handoff Failed</h1>"
        "<p>The Handoff Codex Agent did not satisfy the output contract.</p></body></html>\n",
        encoding="utf-8",
    )
    return f"{report}\nHANDOFF_STATUS: failed\n"


def _handoff_sprint_id(scope_text: str, request: ExecutionRequest) -> str:
    match = SPRINT_SCOPE_PATTERN.search(scope_text)
    if match:
        return match.group(0).lower().replace("_", "-").replace(" ", "-")
    for feature in request.feature_queue:
        sprint_id = str(feature.get("sprint_id") or "").strip()
        if sprint_id:
            return sprint_id
    return "sprint-01"


def _handoff_recovery_candidates(
    target_dir: Path,
    run_dir: Path,
    paths: HandoffContractPaths,
) -> list[tuple[Path, Path]]:
    scoped = [
        (target_dir / paths.summary, run_dir / paths.summary),
        (target_dir / paths.html, run_dir / paths.html),
        (target_dir / paths.evidence, run_dir / paths.evidence),
    ]
    legacy = [
        (target_dir / "handoff" / HANDOFF_SUMMARY_MARKDOWN, run_dir / paths.summary),
        (target_dir / "handoff" / "release-report.html", run_dir / paths.html),
        (target_dir / "handoff" / "release-evidence.json", run_dir / paths.evidence),
        (target_dir / HANDOFF_SUMMARY_MARKDOWN, run_dir / paths.summary),
        (target_dir / HANDOFF_REPORT_HTML, run_dir / paths.html),
        (target_dir / HANDOFF_EVIDENCE_JSON, run_dir / paths.evidence),
    ]
    return scoped + legacy


def _unique_artifacts(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
