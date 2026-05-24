"""Codex CLI runner for the QA specialist agent."""

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
from agentic_company.platform.messages import render_incoming_messages_for_prompt
from agentic_company.platform.models import AgentRunResult, ExecutionRequest
from agentic_company.platform.quality_gates import (
    QualityGateReport,
    QualityGateRunner,
    load_quality_gate_report,
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
    sandbox: str = DEFAULT_CODEX_SANDBOX
    timeout_seconds: int = 3600
    contract_attempts: int = 2
    command_executor: CommandExecutor | None = None
    quality_gate_runner: QualityGateRunner | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        feature = request.active_feature or _active_feature_from_request(request)
        feature_id = str(feature.get("id") or "full-run")
        qa_dir = run_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        report_artifact = f"08-qa-report-{feature_id}.md"
        results_artifact = f"qa/results-{feature_id}.json"
        event_log = run_dir / "events.jsonl"
        execution_id = _execution_id(request, feature_id)

        write_event(
            event_log,
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_started",
            {
                "feature_id": feature_id,
                "target_project_dir": request.target_project_dir,
                "execution_id": execution_id,
            },
        )

        quality_gate_report = (self.quality_gate_runner or QualityGateRunner()).run(
            run_dir,
            request,
            feature,
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
                feature,
                attempt=attempt,
                execution_id=execution_id,
                previous_summary=summary,
                previous_contract_errors=contract_errors,
            )
            summary = attempt_artifacts["summary"]
            returncode = int(attempt_artifacts["returncode"])
            codex_thread_id = str(attempt_artifacts.get("codex_thread_id") or codex_thread_id)
            structured_artifacts.extend(attempt_artifacts["artifacts"])
            contract = _read_qa_contract(run_dir, request, feature_id, summary)
            if returncode == 0 and contract["contract_valid"]:
                break
            contract_errors = list(contract["contract_errors"])

        contract = _read_qa_contract(run_dir, request, feature_id, summary)
        if returncode != 0:
            status = "failed"
            summary = summary or "QA Codex exited non-zero."
        elif not contract["contract_valid"]:
            status = "failed"
            summary = _write_contract_failure_artifacts(
                run_dir,
                feature_id,
                summary,
                contract["contract_errors"],
            )
        else:
            status = str(contract["status"])

        if status == "passed" and quality_gate_report.blocks_release:
            status = "failed"
            summary = _quality_gate_failure_summary(summary, quality_gate_report)
        elif (
            status == "passed"
            and _quality_gate_needs_browser_evidence(quality_gate_report)
            and not _codex_results_include_browser_evidence(run_dir, feature_id)
        ):
            status = "failed"
            summary = _missing_browser_evidence_summary(summary, quality_gate_report)
            _write_missing_browser_repair_artifacts(run_dir, feature_id)
        _merge_quality_gate_into_results(run_dir, feature_id, quality_gate_report, status)
        output_artifacts = _unique_artifacts(
            [
                report_artifact,
                results_artifact,
                *_quality_gate_artifacts(quality_gate_report),
                *structured_artifacts,
                *contract["optional_artifacts"],
                *_fix_request_artifacts(run_dir, feature_id),
            ]
        )
        write_event(
            event_log,
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_completed",
            {
                "feature_id": feature_id,
                "status": status,
                "artifact": report_artifact,
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
            blocking_findings=_qa_blocking_findings(run_dir, feature_id, status),
            fix_request_artifacts=_fix_request_artifacts(run_dir, feature_id),
            recommended_next_action=_qa_recommended_next_action(status),
        )

    def _run_attempt(
        self,
        run_dir: Path,
        request: ExecutionRequest,
        feature: dict[str, Any],
        *,
        attempt: int,
        execution_id: str,
        previous_summary: str,
        previous_contract_errors: Sequence[str],
    ) -> dict[str, Any]:
        feature_id = str(feature.get("id") or "full-run")
        attempt_dir = execution_artifact_dir(
            root=run_dir / "qa" / "codex" / feature_id,
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
            feature,
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
            f"feature_id={feature_id}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n"
            f"attempt={attempt}\n\n"
            "QA Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir / "events.jsonl",
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_attempt_started",
            {
                "feature_id": feature_id,
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
            run_dir / "events.jsonl",
            request.run_id,
            QUALITY_CODEX_AGENT_ID,
            "qa_codex_attempt_completed",
            {
                "feature_id": feature_id,
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


def build_quality_codex_prompt(
    request: ExecutionRequest,
    run_dir: Path,
    feature: dict[str, Any],
    *,
    attempt: int,
    previous_summary: str,
    previous_contract_errors: Sequence[str] | None = None,
) -> str:
    """Build the QA Codex Agent prompt without predefined QA checks."""

    feature_id = str(feature.get("id") or "full-run")
    criteria = "\n".join(f"- {criterion}" for criterion in feature.get("acceptance_criteria", []))
    completed = ", ".join(request.completed_feature_ids) or "none"
    input_artifacts = "\n".join(f"- {artifact}" for artifact in request.input_artifacts)
    expected_outputs = "\n".join(f"- {artifact}" for artifact in request.expected_outputs)
    upstream_messages = render_incoming_messages_for_prompt(run_dir, to_agent="qa-agent")
    report_path = run_dir / f"08-qa-report-{feature_id}.md"
    fallback_report_path = run_dir / "qa" / f"08-qa-report-{feature_id}.md"
    generated_fallback_report_path = (
        Path(request.target_project_dir) / "qa" / f"08-qa-report-{feature_id}.md"
    )
    results_path = run_dir / "qa" / f"results-{feature_id}.json"
    generated_results_path = Path(request.target_project_dir) / "qa" / f"results-{feature_id}.json"
    fix_request_md_path = run_dir / f"10-fix-request-{feature_id}.md"
    fix_request_json_path = run_dir / f"10-fix-request-{feature_id}.json"
    gate_report = load_quality_gate_report(run_dir, feature_id)
    gate_report_path = run_dir / "qa" / "gates" / feature_id / "quality-gate-report.json"
    gate_plan_path = run_dir / "qa" / "gates" / feature_id / "quality-gate-plan.json"
    gate_summary = _quality_gate_prompt_summary(gate_report)
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
You are the sole owner of quality work for the active feature. The platform will
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

Active feature:
- ID: {feature_id}
- Title: {feature.get("title")}

Acceptance criteria:
{criteria or "- None provided."}

Completed features that must not regress: {completed}

Upstream agent messages:
{upstream_messages}

Platform quality gates:
- The platform has already run deterministic quality gates and written:
  - `{gate_plan_path}`
  - `{gate_report_path}`
- You must read and explain this gate evidence in your QA report.
- You may run additional QA checks, but you must not mark QA as passed when a
  required platform gate failed. Failed gates require repair or an explicit
  blocker/limited-evidence explanation.
{gate_summary}

Your job:
- Inspect the requirements, planning artifacts, generated project, and previous
  feature evidence.
- Use the active feature acceptance criteria, canonical work item packet, and
  referenced artifacts as the QA source of truth. Treat coordinator free-text as
  routing/context unless it is supported by those sources.
- Do not fail a feature solely on a stricter requirement introduced only by a
  coordinator paraphrase. If coordinator text conflicts with the canonical work
  item or artifacts, report the contract mismatch and validate the canonical
  acceptance criteria.
- Treat `{run_dir}` as the delivery run workspace and
  `{request.target_project_dir}` as the generated product project.
- Use platform gate evidence as the minimum checklist, then add stronger checks
  if needed for the active feature.
- You may use network-backed tools when needed for QA evidence, including
  package indexes, browser/tool downloads, documentation lookup, Docker, and
  local runtime checks.
- Decide what additional QA evidence is required for this feature beyond the
  platform gates.
- Generate any QA scripts, temporary data, browser checks, runtime checks, or
  other evidence you need.
- Execute the QA work yourself from the generated project workspace.
- Do not ignore platform gate failures or missing browser evidence.
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
- Keep the scope on the active feature plus completed-feature regression risk.

Non-exhaustive QA toolbox:
- Use the following as a starting toolbox, not a complete or limiting checklist.
- Choose the tools, checks, evidence types, and quality dimensions that best fit
  the active feature and generated project.
- You may use other tools or approaches when they provide stronger evidence.
- Possible evidence options include source inspection, API/HTTP checks,
  framework-native testing tools, generated Python QA scripts, Docker Compose
  runtime checks, browser automation, screenshots, browser console/network
  inspection, accessibility-oriented checks, responsive viewport checks,
  performance smoke checks, and deployment/runtime smoke checks when applicable.
- For Streamlit apps, Streamlit AppTest can be useful for component/runtime
  behavior. For realistic browser interaction or visual/layout evidence,
  Playwright can be useful.
- You may install or fetch test-only tools when they are needed for stronger QA
  evidence, for example ephemeral browser automation, accessibility tooling,
  image/screenshot comparison helpers, HTTP clients, or framework-specific test
  helpers. Prefer ephemeral tool installation through the command runner over
  adding test-only packages to the generated product's `pyproject.toml`.
- If installing a useful QA tool fails because of environment, network, browser,
  or platform constraints, report that as a QA evidence limitation and choose
  the next-best evidence path. Do not mark a feature as fully proven if the
  skipped tool was necessary to cover an acceptance criterion.
- Do not treat "tool not currently installed" as a reason to skip an important
  QA dimension. First decide whether the tool is worth installing for the active
  feature; then install it safely or explain why it could not be installed.
- For UI features, evaluate both behavior and user experience when relevant:
  visible state, empty/loading/error states, labels and roles, keyboard path,
  responsive layout, text overlap, broken interactions, and visual polish
  relative to the product intent.
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
- If the sandbox refuses the report path, write it to this fallback path:
  `{fallback_report_path}`
- If the sandbox refuses both run-level paths, write the report and results under
  the generated project fallback directory:
  `{generated_fallback_report_path}`
  `{generated_results_path}`
- End your final message with exactly one status line:
  `QA_STATUS: passed` or `QA_STATUS: failed`.
- If QA fails, also write:
  `{fix_request_md_path}`
  `{fix_request_json_path}`

The results JSON must be valid JSON and include at least:
```json
{{
  "feature_id": "{feature_id}",
  "status": "passed",
  "gate_coverage": [
    {{
      "gate": "static_preflight",
      "status": "passed",
      "evidence": "platform quality gate evidence"
    }}
  ],
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
    feature_id: str,
    summary: str,
) -> dict[str, Any]:
    _recover_misplaced_qa_contract_artifacts(run_dir, Path(request.target_project_dir), feature_id)
    status = _parse_status(summary)
    report_path = run_dir / f"08-qa-report-{feature_id}.md"
    results_path = run_dir / "qa" / f"results-{feature_id}.json"
    errors: list[str] = []
    optional_artifacts: list[str] = []
    if status is None:
        errors.append("QA Codex final message did not include QA_STATUS: passed|failed.")
    if not report_path.exists():
        errors.append(f"Missing required QA report: {report_path.name}.")
    if not results_path.exists():
        errors.append(f"Missing required QA results JSON: qa/results-{feature_id}.json.")
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
    if status == "failed":
        for relative_path in [
            f"10-fix-request-{feature_id}.md",
            f"10-fix-request-{feature_id}.json",
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


def _quality_gate_prompt_summary(report: QualityGateReport | None) -> str:
    if report is None:
        return "- Gate report was not available when prompt was rendered."
    failed = [check for check in report.checks if check.status == "failed"]
    limited = [check for check in report.checks if check.status == "limited"]
    lines = [
        f"- Gate status: `{report.status}`.",
        f"- UI-heavy task: `{str(report.ui_heavy).lower()}`.",
    ]
    if failed:
        lines.append("- Failed gates:")
        lines.extend(f"  - {check.name}: {check.evidence}" for check in failed[:6])
    if limited:
        lines.append("- Limited evidence:")
        lines.extend(f"  - {check.name}: {check.evidence}" for check in limited[:6])
    if report.repair_request:
        lines.append(
            "- Repair request: "
            f"{report.repair_request.responsible_agent} / "
            f"{report.repair_request.failure_signature}."
        )
    return "\n".join(lines)


def _quality_gate_artifacts(report: QualityGateReport) -> list[str]:
    paths = [
        f"qa/gates/{report.work_item_id}/quality-gate-report.json",
        *[artifact.path for artifact in report.artifacts if artifact.path],
    ]
    if report.repair_request:
        paths.extend(
            [
                f"10-fix-request-{report.work_item_id}.md",
                f"10-fix-request-{report.work_item_id}.json",
            ]
        )
    return _unique_artifacts(paths)


def _quality_gate_failure_summary(summary: str, report: QualityGateReport) -> str:
    findings = [
        f"- {check.name}: {check.evidence}" for check in report.checks if check.status == "failed"
    ]
    return "\n".join(
        [
            summary.strip(),
            "",
            "Platform quality gates failed, so QA cannot pass.",
            *findings[:8],
            "",
            "QA_STATUS: failed",
            "",
        ]
    ).strip()


def _quality_gate_needs_browser_evidence(report: QualityGateReport) -> bool:
    return report.ui_heavy and any(
        check.gate in {"browser_smoke", "visual_responsive"}
        and check.name == "browser_url_available"
        and check.status == "limited"
        for check in report.checks
    )


def _codex_results_include_browser_evidence(run_dir: Path, feature_id: str) -> bool:
    results_path = run_dir / "qa" / f"results-{feature_id}.json"
    if not results_path.exists():
        return False
    try:
        payload = json.loads(results_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return False
    evidence_text = json.dumps(payload, sort_keys=True).lower()
    return any(
        token in evidence_text
        for token in ("browser", "screenshot", "playwright", "viewport", "console")
    )


def _missing_browser_evidence_summary(summary: str, report: QualityGateReport) -> str:
    return "\n".join(
        [
            summary.strip(),
            "",
            "Platform quality gates found limited browser evidence for a UI-heavy task.",
            "QA cannot pass until Codex QA supplies browser/screenshot evidence or a "
            "clear blocker.",
            "",
            f"Work item: {report.work_item_id}",
            "",
            "QA_STATUS: failed",
            "",
        ]
    ).strip()


def _write_missing_browser_repair_artifacts(run_dir: Path, feature_id: str) -> None:
    json_path = run_dir / f"10-fix-request-{feature_id}.json"
    md_path = run_dir / f"10-fix-request-{feature_id}.md"
    if json_path.exists() and md_path.exists():
        return
    payload = {
        "work_item_id": feature_id,
        "failure_reason": "qa_failed",
        "responsible_agent": "fullstack-agent",
        "reproduction_steps": [
            "Run QA for the UI-heavy work item.",
            "Open the generated app locally or provide a deployed URL.",
            "Capture browser/screenshot evidence for the primary flow.",
        ],
        "expected_behavior": "QA includes browser or screenshot evidence for UI-heavy work.",
        "actual_behavior": "QA returned passed without browser/screenshot evidence.",
        "evidence_artifact_ids": [],
        "failure_signature": "browser_evidence_missing",
        "recommended_fix_scope": (
            "Make the generated app runnable in a browser and rerun QA with evidence."
        ),
        "blocking_findings": [
            {
                "summary": "Missing browser/screenshot evidence for UI-heavy QA.",
                "evidence": "Quality gate report marked browser evidence as limited.",
            }
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                f"# Fix Request: {feature_id}",
                "",
                "QA cannot pass this UI-heavy work item without browser or screenshot evidence.",
                "",
                "## Required Repair",
                "",
                "- Make the app runnable in a browser.",
                "- Rerun QA and capture browser/screenshot evidence for the primary flow.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _merge_quality_gate_into_results(
    run_dir: Path,
    feature_id: str,
    report: QualityGateReport,
    status: str,
) -> None:
    results_path = run_dir / "qa" / f"results-{feature_id}.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if results_path.exists():
        try:
            loaded = json.loads(results_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded
    payload["feature_id"] = feature_id
    payload["status"] = status
    payload["gate_coverage"] = [
        {
            "gate": check.gate,
            "name": check.name,
            "status": check.status,
            "evidence": check.evidence,
            "failure_signature": check.failure_signature,
        }
        for check in report.checks
    ]
    payload["quality_gate_report"] = f"qa/gates/{feature_id}/quality-gate-report.json"
    payload["failure_signatures"] = report.failure_signatures
    repair_refs = [
        relative
        for relative in [f"10-fix-request-{feature_id}.md", f"10-fix-request-{feature_id}.json"]
        if (run_dir / relative).exists()
    ]
    payload["repair_request_artifact_refs"] = repair_refs
    if status == "failed" and report.repair_request:
        payload["remediation_owner"] = report.repair_request.responsible_agent
        payload["remediation_reason"] = report.repair_request.actual_behavior
    payload.setdefault("checks_performed", [])
    payload.setdefault("acceptance_criteria_coverage", [])
    payload.setdefault("risks", [])
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _recover_misplaced_qa_contract_artifacts(
    run_dir: Path,
    target_dir: Path,
    feature_id: str,
) -> None:
    """Recover QA artifacts when Codex can only write inside generated-project."""

    recoveries = [
        (
            target_dir / f"08-qa-report-{feature_id}.md",
            run_dir / f"08-qa-report-{feature_id}.md",
        ),
        (
            target_dir / "qa" / f"08-qa-report-{feature_id}.md",
            run_dir / f"08-qa-report-{feature_id}.md",
        ),
        (
            run_dir / "qa" / f"08-qa-report-{feature_id}.md",
            run_dir / f"08-qa-report-{feature_id}.md",
        ),
        (
            target_dir / "qa" / f"results-{feature_id}.json",
            run_dir / "qa" / f"results-{feature_id}.json",
        ),
        (
            target_dir / f"10-fix-request-{feature_id}.md",
            run_dir / f"10-fix-request-{feature_id}.md",
        ),
        (
            target_dir / f"10-fix-request-{feature_id}.json",
            run_dir / f"10-fix-request-{feature_id}.json",
        ),
    ]
    for source, destination in recoveries:
        if destination.exists() or not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _parse_status(summary: str) -> str | None:
    match = QA_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _execution_id(request: ExecutionRequest, feature_id: str) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=QUALITY_CODEX_AGENT_ID,
        target=feature_id,
        intent=request.execution_intent or "qa",
    )


def _fix_request_artifacts(run_dir: Path, feature_id: str) -> list[str]:
    return [
        path.relative_to(run_dir).as_posix()
        for path in [
            run_dir / f"10-fix-request-{feature_id}.md",
            run_dir / f"10-fix-request-{feature_id}.json",
        ]
        if path.exists()
    ]


def _qa_blocking_findings(run_dir: Path, feature_id: str, status: str) -> list[str]:
    if status == "passed":
        return []
    fix_json = run_dir / f"10-fix-request-{feature_id}.json"
    if not fix_json.exists():
        return [f"QA failed for feature {feature_id}."]
    try:
        payload = json.loads(fix_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [f"QA failed for feature {feature_id}; fix request JSON is invalid."]
    findings = payload.get("blocking_findings")
    if isinstance(findings, list):
        rendered = []
        for finding in findings:
            if isinstance(finding, dict):
                rendered.append(str(finding.get("summary") or finding.get("evidence") or finding))
            else:
                rendered.append(str(finding))
        return rendered or [str(payload.get("summary") or f"QA failed for feature {feature_id}.")]
    return [str(payload.get("summary") or f"QA failed for feature {feature_id}.")]


def _qa_recommended_next_action(status: str) -> str:
    if status == "passed":
        return "Proceed to the next delivery step."
    return "Send the exact QA fix request artifacts to the owning implementation agent."


def _write_contract_failure_artifacts(
    run_dir: Path,
    feature_id: str,
    summary: str,
    errors: list[str],
) -> str:
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    results_path = qa_dir / f"results-{feature_id}.json"
    report_path = run_dir / f"08-qa-report-{feature_id}.md"
    payload = {
        "feature_id": feature_id,
        "status": "failed",
        "checks_performed": [],
        "acceptance_criteria_coverage": [],
        "risks": ["QA Codex did not satisfy the platform output contract."],
        "contract_errors": errors,
    }
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = "\n".join(
        [
            f"# QA Report: {feature_id}",
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


def _active_feature_from_request(request: ExecutionRequest) -> dict[str, Any]:
    completed = set(request.completed_feature_ids)
    for feature in sorted(
        request.feature_queue,
        key=lambda item: int(item.get("delivery_order", 0)),
    ):
        if str(feature.get("id")) not in completed:
            return feature
    return {"id": "full-run", "title": "Full QA run", "acceptance_criteria": []}


def _unique_artifacts(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
