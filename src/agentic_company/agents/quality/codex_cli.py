"""Codex CLI runner for the QA specialist agent."""

from __future__ import annotations

import json
import logging
import re
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
from agentic_company.platform.artifacts.artifacts import load_execution_request, read_text_artifact
from agentic_company.platform.db.models import AgentRunResult, ExecutionRequest
from agentic_company.platform.mirror.messages import render_incoming_messages_for_prompt
from agentic_company.platform.run.events import write_event
from agentic_company.platform.run.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)

LOGGER = logging.getLogger(__name__)

QUALITY_CODEX_AGENT_ID = "qa-codex-agent"
QA_STATUS_PATTERN = re.compile(
    r"^QA_STATUS:\s*(passed|failed)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class QualityCodexRunner:
    """Run QA as a Codex-owned specialist execution.

    The platform does not choose QA checks. It only invokes the QA Codex Agent,
    captures evidence, enforces the output contract, and returns the parsed verdict.
    """

    codex_binary: str | None = None
    # QA runs browser smoke + may merge the PR via the git-pr-workflow skill — full host access.
    sandbox: str = "danger-full-access"
    timeout_seconds: int = 3600
    contract_attempts: int = 2
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        work_item = _work_item_from_request(request)
        work_item_id = str(work_item["work_item_id"])
        qa_dir = run_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        report_artifact = f"08-qa-report-{work_item_id}.md"
        results_artifact = f"qa/results-{work_item_id}.json"
        event_log = run_dir
        execution_id = _execution_id(request, work_item_id)

        write_event(
            event_log,
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_started",
            {
                "work_item_id": work_item_id,
                "target_project_dir": request.target_project_dir,
                "execution_id": execution_id,
            },
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
                work_item,
                attempt=attempt,
                execution_id=execution_id,
                previous_summary=summary,
                previous_contract_errors=contract_errors,
            )
            summary = attempt_artifacts["summary"]
            returncode = int(attempt_artifacts["returncode"])
            codex_thread_id = str(attempt_artifacts.get("codex_thread_id") or codex_thread_id)
            structured_artifacts.extend(attempt_artifacts["artifacts"])
            if returncode != 0 and _is_provider_limit(summary):
                break
            contract = _read_qa_contract(run_dir, request, work_item_id, summary)
            if returncode == 0 and contract["contract_valid"]:
                break
            contract_errors = list(contract["contract_errors"])

        contract = _read_qa_contract(run_dir, request, work_item_id, summary)
        provider_limit = returncode != 0 and _is_provider_limit(summary)
        if provider_limit:
            status = "provider_limit"
            summary = summary or "QA Codex could not run because provider usage limit was reached."
        elif returncode != 0:
            status = "failed"
            summary = _write_contract_failure_artifacts(
                run_dir,
                work_item_id,
                summary or "QA Codex exited non-zero.",
                [f"QA Codex exited non-zero with returncode {returncode}."],
            )
        elif not contract["contract_valid"]:
            status = "failed"
            summary = _write_contract_failure_artifacts(
                run_dir,
                work_item_id,
                summary,
                contract["contract_errors"],
            )
        else:
            status = str(contract["status"])

        output_artifacts = _existing_artifacts(
            run_dir,
            _unique_artifacts(
                [
                    *([] if provider_limit else [report_artifact, results_artifact]),
                    *structured_artifacts,
                    *contract["optional_artifacts"],
                    *_fix_request_artifacts(run_dir, work_item_id),
                ]
            ),
        )
        write_event(
            event_log,
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_completed",
            {
                "work_item_id": work_item_id,
                "status": status,
                "artifact": report_artifact if not provider_limit else "",
                "execution_id": execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=QUALITY_CODEX_AGENT_ID,
            status=f"qa_{status}",
            output_artifacts=output_artifacts,
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=_qa_blocking_findings(run_dir, work_item_id, status),
            fix_request_artifacts=_fix_request_artifacts(run_dir, work_item_id),
            recommended_next_action=_qa_recommended_next_action(status),
        )

    def _run_attempt(
        self,
        run_dir: Path,
        request: ExecutionRequest,
        work_item: dict[str, Any],
        *,
        attempt: int,
        execution_id: str,
        previous_summary: str,
        previous_contract_errors: Sequence[str],
    ) -> dict[str, Any]:
        work_item_id = str(work_item["work_item_id"])
        attempt_dir = execution_artifact_dir(
            root=run_dir / "qa" / "codex" / work_item_id,
            execution_id=execution_id,
            attempt=attempt,
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=QUALITY_CODEX_AGENT_ID,
            attempt=attempt,
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        summary_path = attempt_dir / "summary.md"
        prompt_path = attempt_dir / "prompt.md"
        log_path = attempt_dir / "execution.log"
        raw_events_path = attempt_dir / "events.jsonl"
        if raw_events_path.exists():
            raw_events_path.unlink()

        prompt = build_quality_codex_prompt(
            request,
            run_dir,
            work_item,
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
            f"agent_id={QUALITY_CODEX_AGENT_ID}\n"
            f"work_item_id={work_item_id}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n"
            f"attempt={attempt}\n\n"
            "QA Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir,
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_attempt_started",
            {
                "work_item_id": work_item_id,
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
                agent_id=QUALITY_CODEX_AGENT_ID,
                work_item_id=work_item_id,
            )
        except FileNotFoundError:
            LOGGER.exception("QA Codex CLI missing run_id=%s", request.run_id)
            summary_path.write_text(
                "QA_STATUS: failed\n\nCodex CLI was not found.\n",
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
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_attempt_completed",
            {
                "work_item_id": work_item_id,
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
            "artifacts": _existing_artifacts(
                run_dir,
                [
                    summary_path.relative_to(run_dir).as_posix(),
                    prompt_path.relative_to(run_dir).as_posix(),
                    log_path.relative_to(run_dir).as_posix(),
                    *structured_artifacts,
                ],
            ),
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


def build_quality_codex_prompt(
    request: ExecutionRequest,
    run_dir: Path,
    work_item: dict[str, Any],
    *,
    attempt: int,
    previous_summary: str,
    previous_contract_errors: Sequence[str] | None = None,
) -> str:
    """Build the QA Codex Agent prompt without predefined QA checks."""

    work_item_id = str(work_item["work_item_id"])
    criteria = "\n".join(f"- {criterion}" for criterion in work_item.get("acceptance_criteria", []))
    completed = ", ".join(request.completed_work_item_ids) or "none"
    input_artifacts = "\n".join(f"- {artifact}" for artifact in request.input_artifacts)
    expected_outputs = "\n".join(f"- {artifact}" for artifact in request.expected_outputs)
    instructions = "\n".join(f"- {instruction}" for instruction in request.instructions)
    upstream_messages = render_incoming_messages_for_prompt(run_dir, to_agent="qa-agent")
    report_path = run_dir / f"08-qa-report-{work_item_id}.md"
    results_path = run_dir / "qa" / f"results-{work_item_id}.json"
    fix_request_md_path = run_dir / f"10-fix-request-{work_item_id}.md"
    fix_request_json_path = run_dir / f"10-fix-request-{work_item_id}.json"
    repair_note = ""
    if attempt > 1:
        contract_error_lines = "\n".join(f"- {error}" for error in (previous_contract_errors or []))
        repair_note = f"""
Your previous QA Codex attempt did not satisfy the output contract. Complete QA
again if needed, then write the required artifacts and final status.

Previous contract errors:
{contract_error_lines or "- Unknown contract error."}

Previous final message:
{previous_summary or "(empty)"}
"""

    return f"""You are the QA Codex Agent for agentic-company.

Your agent id is `{QUALITY_CODEX_AGENT_ID}`.
You are the sole owner of quality work for the explicit work item. The platform will
not run a predefined QA checklist for you.

Generated project directory:
{request.target_project_dir}

Planning run directory:
{run_dir}

Platform execution id:
{request.execution_id or "(not provided)"}

Execution intent:
{request.execution_intent or "(not provided)"}

Input artifacts:
{input_artifacts or "- None"}

Expected implementation outputs from planning:
{expected_outputs or "- None"}

Execution instructions:
{instructions or "- None"}

Work item:
- ID: {work_item_id}
- Title: {work_item.get("title")}

Acceptance criteria:
{criteria or "- None provided."}

Completed work items that must not regress: {completed}

Upstream agent messages:
{upstream_messages}

Your job:
- Inspect the requirements, planning artifacts, generated project, and previous
  Work item evidence.
- Use the work-item acceptance criteria, canonical work item packet, and
  referenced artifacts as the QA source of truth. Treat coordinator free-text as
  routing/context unless it is supported by those sources.
- Do not fail a work item solely on a stricter requirement introduced only by a
  coordinator paraphrase. If coordinator text conflicts with the canonical work
  item or artifacts, report the contract mismatch and validate the canonical
  acceptance criteria.
- Validate ONLY this work item's acceptance criteria. Capabilities the plan
  scopes to a LATER work item are out of scope here: do not require them, and do
  NOT count them as this item's passing evidence even if they happen to be
  present — they are proven when that item runs, and the whole-product contract
  is validated only at the final / deployment QA smoke. Crediting a future item's
  feature here blurs the sprint boundary and is a QA contract violation.
- Treat `{run_dir}` as the delivery run workspace and
  `{request.target_project_dir}` as the generated product project.
- Design the QA approach yourself from the work item contract, artifacts, and
  acceptance criteria. Do not rely on a hardcoded platform checklist.
- You may use network-backed tools when needed for QA evidence, including
  package indexes, browser/tool downloads, documentation lookup, Docker, and
  local runtime checks.
- Decide what QA evidence is required for this work item.
- Generate any QA scripts, temporary data, browser checks, runtime checks, or
  other evidence you need.
- Execute the QA work yourself from the generated project workspace.
- Do not modify files outside `{run_dir}`. In particular, do not modify the
  platform repository source, root configuration, user home files, or unrelated
  projects.
- Do not modify product implementation files. If you need QA-only helper files,
  put them under `{run_dir}\\qa`.
- If a safe runtime configuration file is needed only to run QA, you may create
  it from non-secret example values and document that in the report.
- Avoid destructive actions and do not remove containers, images, volumes, or
  user data as part of QA.
- Local QA may create `.venv`, browser caches, `.pytest_cache`, screenshots, or
  other tool artifacts. Keep them out of Docker/image contexts where relevant,
  but do not fail QA only because a local cache remains.
- Do not recursively list dependency/cache directories such as `.venv`,
  `node_modules`, Playwright browser caches, `.pytest_cache`, or Docker build
  output directories.
- Do not stop broad sets of processes by matching the generated project path.
  Only stop exact process IDs that QA started and still owns.
- Keep the scope on the explicit work item plus completed work-item regression risk.

Non-exhaustive QA toolbox:
- Use the following as a starting toolbox, not a complete or limiting checklist.
- Choose the tools, checks, evidence types, and quality dimensions that best fit
  the work item and generated project.
- You may use other tools or approaches when they provide stronger evidence.
- Possible evidence options include source inspection, API/HTTP checks,
  framework-native testing tools, generated Python QA scripts, Docker Compose
  runtime checks, browser automation, screenshots, browser console/network
  inspection, accessibility-oriented checks, responsive viewport checks,
  performance smoke checks, and deployment/runtime smoke checks when applicable.
- For web UI features, produce browser-level evidence with Playwright or an
  equivalent real browser automation path whenever possible. Source inspection
  alone is not enough for UI behavior, layout, forms, navigation, or button
  flows.
- Playwright + Chromium are ALREADY pre-provisioned on this host:
  `PLAYWRIGHT_BROWSERS_PATH` and `NODE_PATH` are already set in your environment
  and point at the repo-local QA runtime. Do NOT run `npm install`,
  `npm install @playwright/test`, or `npx playwright install` — the browser is
  already downloaded and re-downloading reliably times out. Just `require("playwright")`
  from a small Node script and launch Chromium headless. On Windows the PowerShell
  execution policy blocks `npm.ps1`/`npx.ps1`, so call the `.cmd` shims
  (`npm.cmd` / `npx.cmd`) when you must call npm at all. Follow the
  `browser-smoke-qa` skill for the canonical script and the mandatory
  `--disable-gpu`/`--disable-dev-shm-usage` launch flags.
- For Streamlit apps, Streamlit AppTest can be useful for component/runtime
  behavior, but browser evidence is still preferred when visual or interaction
  behavior matters.
- You may install or fetch additional test-only tools (accessibility tooling,
  image/screenshot comparison helpers, HTTP clients, framework-specific test
  helpers) when a work item genuinely needs them, through the command runner
  rather than the generated product's `pyproject.toml`. This does NOT apply to
  Playwright/Chromium, which are pre-provisioned — never reinstall those.
- If installing a useful QA tool fails because of environment, network, browser,
  or platform constraints, report that as a QA evidence limitation and choose
  the next-best evidence path. Do not mark a work item as fully proven if the
  skipped tool was necessary to cover an acceptance criterion.
- Do not treat "tool not currently installed" as a reason to skip an important
  QA dimension. First decide whether the tool is worth installing for the active
  work item; then install it safely or explain why it could not be installed.
- For UI features, evaluate both behavior and user experience when relevant:
  visible state, empty/loading/error states, labels and roles, keyboard path,
  responsive layout, text overlap, broken interactions, and visual polish
  relative to the product intent.
- For UI features, explicitly report whether admin/moderation controls are
  visible only to roles that may use them, whether every visible primary control
  works now or is honestly unavailable, whether demo/static data is clearly not
  masking delivered functionality, and whether any acceptance criterion is only
  covered by API calls while the requirement asks for a visible UI flow.
- For post-deploy QA, open the public deployed URL and verify that the deployed
  runtime matches the expected product behavior and visual state: CSS/static
  assets load, navigation and primary controls still work, no obvious layout
  shift/regression appears versus local/build evidence, and desktop/mobile
  viewports remain usable. If you cannot inspect the deployed page in a browser,
  mark the evidence as limited and do not claim deployed visual quality is fully
  proven.
- For deployed or deployment-adjacent validation, classify failures for Team
  Lead routing. Application behavior, runtime assumptions, startup behavior,
  persistence behavior, API/UI defects, and container definition issues usually
  need Fullstack repair. Cloud resources, secrets/configuration, registry,
  ingress, rollout, scaling, and deployment wiring usually need Deployment
  repair. Include evidence and the likely owner in the report and fix request.
- Do not run every possible tool mechanically. Explain why the evidence you
  chose is sufficient, and call out any remaining evidence gaps as risks.

Required output contract:
- Write the QA results JSON to this exact path:
  `{results_path}`
- Write the QA Markdown report to this exact path:
  `{report_path}`
- End your final message with exactly one status line:
  `QA_STATUS: passed` or `QA_STATUS: failed`.
- If QA fails, also write:
  `{fix_request_md_path}`
  `{fix_request_json_path}`

The results JSON must be valid JSON and include at least:
```json
{{
  "work_item_id": "{work_item_id}",
  "status": "passed",
  "gate_coverage": [],
  "checks_performed": [
    {{
      "name": "QA Agent decided check name",
      "status": "passed",
      "evidence": "What was inspected or executed"
    }}
  ],
  "acceptance_criteria_coverage": [
    {{
      "criterion": "acceptance criterion text",
      "status": "covered",
      "evidence": "specific evidence"
    }}
  ],
  "browser_automation": {{
    "used": true,
    "tool": "Playwright or equivalent, or not used",
    "limitation": "empty when browser evidence was sufficient"
  }},
  "screenshots": [
    {{
      "path": "qa/screenshot-name.png",
      "viewport": "desktop | mobile | other",
      "what_it_shows": "specific visible evidence"
    }}
  ],
  "visible_ui_flows_tested": [],
  "role_specific_control_visibility": [],
  "dead_or_future_controls_found": [],
  "api_only_gaps": [],
  "responsive_viewports": {{
    "desktop": "passed | failed | limited",
    "mobile": "passed | failed | limited",
    "evidence": "specific evidence"
  }},
  "css_static_assets": {{
    "status": "passed | failed | limited",
    "evidence": "CSS/static asset load evidence"
  }},
  "risks": [],
  "failure_signatures": [],
  "repair_request_artifact_refs": [],
  "remediation_owner": "fullstack-agent | deployment-agent | none",
  "remediation_reason": "brief owner-routing explanation when status is failed"
}}
```

The Markdown report should summarize what you tested, what evidence you gathered,
what passed or failed, and what risks remain.
{repair_note}
"""


def _read_qa_contract(
    run_dir: Path,
    request: ExecutionRequest,
    work_item_id: str,
    summary: str,
) -> dict[str, Any]:
    status = _parse_status(summary)
    report_path = run_dir / f"08-qa-report-{work_item_id}.md"
    results_path = run_dir / "qa" / f"results-{work_item_id}.json"
    errors: list[str] = []
    optional_artifacts: list[str] = []
    if status is None:
        errors.append("QA Codex final message did not include QA_STATUS: passed|failed.")
    if not report_path.exists():
        errors.append(f"Missing required QA report: {report_path.name}.")
    if not results_path.exists():
        errors.append(f"Missing required QA results JSON: qa/results-{work_item_id}.json.")
    else:
        try:
            payload = json.loads(results_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"QA results JSON is invalid: {exc}.")
        else:
            result_status = str(payload.get("status", "")).lower()
            if result_status not in {"passed", "failed"}:
                errors.append("QA results JSON must include status passed|failed.")
            elif status and result_status != status:
                errors.append("QA results JSON status does not match final QA_STATUS line.")
            errors.extend(_missing_ui_evidence_fields(payload))
    if status == "failed":
        for relative_path in [
            f"10-fix-request-{work_item_id}.md",
            f"10-fix-request-{work_item_id}.json",
        ]:
            if (run_dir / relative_path).exists():
                optional_artifacts.append(relative_path)
            else:
                errors.append(f"Missing required failure artifact: {relative_path}.")
    return {
        "status": status or "failed",
        "contract_valid": not errors,
        "contract_errors": errors,
        "optional_artifacts": optional_artifacts,
    }


def _missing_ui_evidence_fields(payload: dict[str, Any]) -> list[str]:
    required = [
        "browser_automation",
        "screenshots",
        "visible_ui_flows_tested",
        "role_specific_control_visibility",
        "dead_or_future_controls_found",
        "api_only_gaps",
        "responsive_viewports",
        "css_static_assets",
    ]
    return [
        f"QA results JSON must include evidence field `{field}`."
        for field in required
        if field not in payload
    ]


def _parse_status(summary: str) -> str | None:
    match = QA_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _execution_id(request: ExecutionRequest, work_item_id: str) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=QUALITY_CODEX_AGENT_ID,
        correlation_id=work_item_id,
        intent=request.execution_intent or "qa",
    )


def _fix_request_artifacts(run_dir: Path, work_item_id: str) -> list[str]:
    return [
        path.relative_to(run_dir).as_posix()
        for path in [
            run_dir / f"10-fix-request-{work_item_id}.md",
            run_dir / f"10-fix-request-{work_item_id}.json",
        ]
        if path.exists()
    ]


def _qa_blocking_findings(run_dir: Path, work_item_id: str, status: str) -> list[str]:
    if status == "passed":
        return []
    if status == "provider_limit":
        return [f"QA could not run for work item {work_item_id}: provider usage limit reached."]
    fix_json = run_dir / f"10-fix-request-{work_item_id}.json"
    if not fix_json.exists():
        return [f"QA failed for work item {work_item_id}."]
    try:
        payload = json.loads(fix_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [f"QA failed for work item {work_item_id}; fix request JSON is invalid."]
    findings = payload.get("blocking_findings")
    if isinstance(findings, list):
        rendered = []
        for finding in findings:
            if isinstance(finding, dict):
                rendered.append(str(finding.get("summary") or finding.get("evidence") or finding))
            else:
                rendered.append(str(finding))
        summary = payload.get("summary") or f"QA failed for work item {work_item_id}."
        return rendered or [str(summary)]
    return [str(payload.get("summary") or f"QA failed for work item {work_item_id}.")]


def _qa_recommended_next_action(status: str) -> str:
    if status == "passed":
        return "Proceed to the next delivery step."
    if status == "provider_limit":
        return "Wait for provider capacity or credits, then rerun QA for the same work item."
    return "Send the exact QA fix request artifacts to the owning implementation agent."


def _write_contract_failure_artifacts(
    run_dir: Path,
    work_item_id: str,
    summary: str,
    errors: list[str],
) -> str:
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    results_path = qa_dir / f"results-{work_item_id}.json"
    report_path = run_dir / f"08-qa-report-{work_item_id}.md"
    payload = {
        "work_item_id": work_item_id,
        "status": "failed",
        "checks_performed": [],
        "acceptance_criteria_coverage": [],
        "risks": ["QA Codex did not satisfy the platform output contract."],
        "contract_errors": errors,
    }
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = "\n".join(
        [
            f"# QA Report: {work_item_id}",
            "",
            "Status: failed",
            "",
            "The QA Codex Agent did not satisfy the required output contract.",
            "",
            "## Contract Errors",
            "",
            *[f"- {error}" for error in errors],
            "",
            "## Last QA Codex Message",
            "",
            "```text",
            summary or "(empty)",
            "```",
            "",
        ]
    )
    report_path.write_text(report, encoding="utf-8")
    return f"{report}\nQA_STATUS: failed\n"


def _work_item_from_request(request: ExecutionRequest) -> dict[str, Any]:
    work_item = dict(request.work_item)
    work_item_id = str(work_item.get("work_item_id") or "").strip()
    if not work_item_id:
        raise ValueError("QA execution request is missing explicit work_item.work_item_id")
    return work_item


def _unique_artifacts(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _existing_artifacts(run_dir: Path, paths: list[str]) -> list[str]:
    return [path for path in paths if (run_dir / path).is_file()]


def _is_provider_limit(summary: str) -> bool:
    normalized = summary.lower()
    return any(
        marker in normalized
        for marker in (
            "usage limit",
            "purchase more credits",
            "quota",
            "rate limit",
            "provider limit",
            "provider_limit",
        )
    )
