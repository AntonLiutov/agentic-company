"""Codex CLI runner for the autonomous Handoff Agent."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_company.agents.handoff.contracts import (
    FINAL_PROJECT_REPORT_SCOPE,
    SPRINT_HANDOFF_SCOPE,
    HandoffContractPaths,
    handoff_contract_paths_for_scope,
)
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
HANDOFF_REPORT_HTML = "handoff/release-report.html"

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
    sandbox: str = DEFAULT_CODEX_SANDBOX
    timeout_seconds: int = 1800
    contract_attempts: int = 1
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        contract_paths = handoff_contract_paths(request, run_dir)
        event_log = run_dir
        execution_id = _execution_id(request)
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_started",
            {"target_project_dir": request.target_project_dir, "execution_id": execution_id},
        )

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

        output_artifacts = [contract_paths.html]
        write_event(
            event_log,
            request.run_id,
            HANDOFF_CODEX_AGENT_ID,
            "handoff_codex_completed",
            {
                "status": status,
                "artifact": contract_paths.html,
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
            run_dir,
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
                run_dir=run_dir,
                run_id=request.run_id,
                agent_id=HANDOFF_CODEX_AGENT_ID,
                work_item_id=str(request.work_item.get("work_item_id") or ""),
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
            run_dir,
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
        run_dir: Path,
        run_id: int | str,
        agent_id: str,
        work_item_id: str | None,
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
            trace_run_dir=run_dir,
            trace_run_id=run_id,
            trace_agent_id=agent_id,
            trace_work_item_id=work_item_id,
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

    contract_paths = handoff_contract_paths(request, run_dir)
    html_path = run_dir / contract_paths.html
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

Handoff scope contract:
- handoff_scope: {request.handoff_scope or "(missing)"}
- handoff_sprint_id: {request.handoff_sprint_id or "(none)"}
- handoff_output_dir: {request.handoff_output_dir or "(not provided)"}
- handoff_expected_outputs:
{json.dumps(request.handoff_expected_outputs, indent=2)}

Release context:
- Work item and acceptance criteria are in upstream planning artifacts and the
  current delivery execution request.
- Fullstack summaries and Codex logs describe what was built.
- QA reports/results describe feature validation.
- Deployment artifacts describe public URLs, cloud resources, risks, and
  post-deploy targets.
- Use the execution request and explicit DB work-item packet as the runtime contract.
- Structured trace and the artifact registry are the product source of truth.

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
  - one client-facing HTML report for the delivered sprint/release.
- In the HTML, explain what was delivered, what was validated, what is limited,
  how to try or review it when a usable URL/instruction exists, and what decision
  or next step is expected.
- The HTML report is stakeholder-facing. It must contain only high-level,
  business-friendly, user-friendly information. Do not expose
  internal file paths, local paths, artifact paths, run folders, console paths,
  execution IDs, JSON filenames, log filenames, prompt filenames, event filenames,
  hidden state filenames, dependency/cache folders, or source-code locations in
  the report.
- Treat raw artifact paths from the output contract, upstream messages, and
  delivery state as internal routing metadata. Use them to find evidence and to
  write the required HTML file, but never copy them into the human-readable
  report.
- Translate implementation, QA, deployment, and handoff evidence into plain
  business language: what the user can do, what was validated, how to try the
  app if a public link exists, what is limited, and what should happen next.
- Do not include internal technical sections such as "Artifacts", "Evidence
  paths", "Console logs", "JSON files", "Execution details", or "Source files"
  in the HTML report.
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
  - `{html_path}`
- Handoff-owned helper files, screenshots, transcripts, or source evidence
  belong under `{run_dir}\\handoff`.
- Do not modify product implementation files.
- Do not write QA or deployment artifacts. Those belong to other agents.
- Do not print or expose secret values.
- Do not recursively list dependency/cache directories such as `.venv`,
  `node_modules`, browser caches, `.pytest_cache`, `dist`, or `build`. Handoff
  should cite relevant reports and evidence, not dump tool caches.
- Do not delete generated project caches or stop runtime processes. Handoff is
  an evidence-packaging role and should not perform cleanup.

Minimal output contract:
- Write `{html_path}` as the only handoff report artifact.
- The report must be print-friendly HTML.
- Do not write a Markdown handoff summary.
- Do not write a JSON handoff evidence file.
- End your final message with a short status when useful, for example
  `HANDOFF_STATUS: ready`, `HANDOFF_STATUS: blocked`,
  `HANDOFF_STATUS: failed`, or `HANDOFF_STATUS: unknown`.

If the handoff is ready, the HTML should be understandable by a non-engineering
stakeholder. For `sprint_handoff`, keep it scoped to the named sprint only.
For `final_project_report`, consolidate completed sprint handoffs
and project-level evidence. If blocked, failed, or unknown, explain exactly
which evidence prevents client-ready handoff and what should happen next.
{repair_note}
"""


def handoff_contract_paths(request: ExecutionRequest, run_dir: Path) -> HandoffContractPaths:
    """Return scoped handoff artifact paths from the explicit Handoff request."""

    return handoff_contract_paths_for_scope(
        request.handoff_scope,
        sprint_id=request.handoff_sprint_id,
    )


def read_handoff_contract(
    run_dir: Path,
    target_dir: Path,
    summary: str,
    *,
    paths: HandoffContractPaths | None = None,
) -> dict[str, Any]:
    contract_paths = paths or HandoffContractPaths(
        html=HANDOFF_REPORT_HTML,
    )
    errors: list[str] = []

    if not (run_dir / contract_paths.html).exists():
        errors.append(f"Missing required handoff HTML report: {contract_paths.html}.")

    status = _parse_status(summary)
    if status is None and not errors:
        status = "ready"

    return {
        "status": status or "failed",
        "contract_valid": not errors,
        "contract_errors": errors,
    }


def _parse_status(summary: str) -> str | None:
    match = HANDOFF_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _execution_id(request: ExecutionRequest) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=HANDOFF_CODEX_AGENT_ID,
        correlation_id=(
            request.handoff_sprint_id
            if request.handoff_scope == SPRINT_HANDOFF_SCOPE
            else FINAL_PROJECT_REPORT_SCOPE
        ),
        intent=request.execution_intent or "handoff",
    )


def _write_contract_failure_artifacts(
    run_dir: Path,
    summary: str,
    errors: list[str],
    *,
    paths: HandoffContractPaths,
) -> str:
    (run_dir / paths.html).parent.mkdir(parents=True, exist_ok=True)
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
    (run_dir / paths.html).write_text(
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
