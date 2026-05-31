"""Codex CLI runner for the autonomous Deployment Agent."""

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
from agentic_company.platform.state import DELIVERY_STATE_SNAPSHOT

LOGGER = logging.getLogger(__name__)

DEPLOYMENT_CODEX_AGENT_ID = "deployment-codex-agent"
DEPLOYMENT_STATUS_PATTERN = re.compile(
    r"^DEPLOYMENT_STATUS:\s*(deployed|blocked|failed|unknown)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
DEPLOYMENT_STATUSES = {"deployed", "blocked", "failed", "unknown"}
DEPLOYMENT_RESULT_JSON = "deployment/result.json"
DEPLOYMENT_PLAN_JSON = "11-deployment-plan.json"
DEPLOYMENT_PLAN_MARKDOWN = "11-deployment-plan.md"
DEPLOYMENT_REQUEST_JSON = "12-deployment-request.json"
DEPLOYMENT_REQUEST_MARKDOWN = "12-deployment-request.md"
DEPLOYMENT_SUMMARY_MARKDOWN = "13-deployment-summary.md"

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class DeploymentCodexRunner:
    """Run deployment as a Codex-owned specialist execution.

    The platform does not infer topology, choose Azure commands, or decide which
    containers exist. The Deployment Codex Agent inspects the generated project
    and owns those decisions. This runner only captures evidence and validates
    the output contract.
    """

    codex_binary: str | None = None
    sandbox: str = DEFAULT_CODEX_SANDBOX
    timeout_seconds: int = 3600
    contract_attempts: int = 2
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = load_execution_request(run_dir)
        event_log = run_dir
        execution_id = _execution_id(request)
        write_event(
            event_log,
            request.run_id,
            DEPLOYMENT_CODEX_AGENT_ID,
            "deployment_codex_started",
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
            contract = read_deployment_contract(run_dir, Path(request.target_project_dir), summary)
            if returncode == 0 and contract["contract_valid"]:
                break
            contract_errors = list(contract["contract_errors"])

        contract = read_deployment_contract(run_dir, Path(request.target_project_dir), summary)
        if returncode != 0:
            status = "failed"
            summary = summary or "Deployment Codex exited non-zero."
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
                DEPLOYMENT_RESULT_JSON,
                DEPLOYMENT_PLAN_JSON,
                DEPLOYMENT_PLAN_MARKDOWN,
                DEPLOYMENT_REQUEST_JSON,
                DEPLOYMENT_REQUEST_MARKDOWN,
                DEPLOYMENT_SUMMARY_MARKDOWN,
                *structured_artifacts,
                *contract["optional_artifacts"],
            ]
        )
        write_event(
            event_log,
            request.run_id,
            DEPLOYMENT_CODEX_AGENT_ID,
            "deployment_codex_completed",
            {
                "status": status,
                "artifact": DEPLOYMENT_SUMMARY_MARKDOWN,
                "execution_id": execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=DEPLOYMENT_CODEX_AGENT_ID,
            status=f"deployment_{status}",
            output_artifacts=output_artifacts,
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=[] if status == "deployed" else [summary.strip()[:500]],
            recommended_next_action=(
                "Proceed to post-deploy QA."
                if status == "deployed"
                else (
                    "Return findings to Team Lead for remediation routing. Route app/runtime "
                    "cloud-readiness gaps to Fullstack; route Azure resource, secret, ingress, "
                    "registry, rollout, and deployment configuration issues back to Deployment."
                )
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
            root=run_dir / "deployment" / "codex",
            execution_id=execution_id,
            attempt=attempt,
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=DEPLOYMENT_CODEX_AGENT_ID,
            attempt=attempt,
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        summary_path = attempt_dir / "summary.md"
        prompt_path = attempt_dir / "prompt.md"
        log_path = attempt_dir / "execution.log"
        raw_events_path = attempt_dir / "events.jsonl"
        if raw_events_path.exists():
            raw_events_path.unlink()

        prompt = build_deployment_codex_prompt(
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
            f"agent_id={DEPLOYMENT_CODEX_AGENT_ID}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n"
            f"attempt={attempt}\n\n"
            "Deployment Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir,
            request.run_id,
            DEPLOYMENT_CODEX_AGENT_ID,
            "deployment_codex_attempt_started",
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
                agent_id=DEPLOYMENT_CODEX_AGENT_ID,
                work_item_id="DEPLOY",
            )
        except FileNotFoundError:
            LOGGER.exception("Deployment Codex CLI missing run_id=%s", request.run_id)
            summary_path.write_text(
                "DEPLOYMENT_STATUS: failed\n\nCodex CLI was not found.\n",
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
            DEPLOYMENT_CODEX_AGENT_ID,
            "deployment_codex_attempt_completed",
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


def build_deployment_codex_prompt(
    request: ExecutionRequest,
    run_dir: Path,
    *,
    attempt: int,
    previous_summary: str,
    previous_contract_errors: Sequence[str] | None = None,
) -> str:
    """Build the Deployment Codex Agent prompt without topology hardcoding."""

    release_context = _deployment_release_context(run_dir, request)
    input_artifacts = "\n".join(f"- {artifact}" for artifact in request.input_artifacts)
    expected_outputs = "\n".join(f"- {artifact}" for artifact in request.expected_outputs)
    completed_features = ", ".join(release_context["completed_feature_ids"]) or "none"
    release_scope = ", ".join(release_context["release_scope"]) or "none"
    feature_queue = "\n".join(
        f"- {feature.get('id')}: {feature.get('title')}"
        for feature in sorted(
            request.feature_queue,
            key=lambda item: int(item.get("delivery_order", 0)),
        )
    )
    target_dir = Path(request.target_project_dir)
    upstream_messages = render_incoming_messages_for_prompt(run_dir, to_agent="deployment-agent")
    result_path = run_dir / DEPLOYMENT_RESULT_JSON
    plan_json_path = run_dir / DEPLOYMENT_PLAN_JSON
    plan_markdown_path = run_dir / DEPLOYMENT_PLAN_MARKDOWN
    request_json_path = run_dir / DEPLOYMENT_REQUEST_JSON
    request_markdown_path = run_dir / DEPLOYMENT_REQUEST_MARKDOWN
    summary_markdown_path = run_dir / DEPLOYMENT_SUMMARY_MARKDOWN
    fallback_result_path = target_dir / DEPLOYMENT_RESULT_JSON
    fallback_deployment_dir = target_dir / "deployment"
    repair_note = ""
    if attempt > 1:
        contract_error_lines = "\n".join(f"- {error}" for error in (previous_contract_errors or []))
        repair_note = f"""
Your previous Deployment Codex attempt did not satisfy the output contract.
Complete or report deployment again, then write the required artifacts.

Previous contract errors:
{contract_error_lines or "- Unknown contract error."}

Previous final message:
{previous_summary or "(empty)"}
"""

    return f"""You are the Deployment Codex Agent for agentic-company.

Your agent id is `{DEPLOYMENT_CODEX_AGENT_ID}`.
You are the sole owner of deployment work for this release batch. The platform
will not infer topology, choose services, choose commands, or run a predefined
deployment checklist for you.

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

Completed implementation features in this release batch: {completed_features}

Deployment release scope: {release_scope}

Feature queue:
{feature_queue or "- None"}

Upstream agent messages:
{upstream_messages}

Workspace ownership:
- Treat `{run_dir}` as the delivery run workspace and
  `{request.target_project_dir}` as the generated product project.
- You may use network-backed tools needed for delivery, including package
  indexes, documentation lookup, Docker daemon access, registry push/pull,
  Azure CLI, HTTP smoke tests, and browser checks.
- Do not modify files outside `{run_dir}`. In particular, do not modify the
  platform repository source, root configuration, user home files, or unrelated
  projects. Reading authenticated tool profiles is allowed when Docker or Azure
  need them for this deployment.
- Treat `{request.target_project_dir}` as the generated product project. Read it
  deeply, but do not rewrite product implementation files unless a deployment
  runtime config file is required and safe.
- Deployment-owned contract artifacts belong at these exact planning-run paths:
  - `{result_path}`
  - `{plan_json_path}` and `{plan_markdown_path}`
  - `{request_json_path}` and `{request_markdown_path}`
  - `{summary_markdown_path}`
- Deployment-owned helper files, scripts, command logs, screenshots, transcripts,
  and cloud/runtime evidence belong under `{run_dir}\\deployment`.
- If the sandbox only allows writing inside the generated project, mirror the
  same deployment-owned contract artifacts under `{fallback_deployment_dir}`.
  At minimum, fallback must include `{fallback_result_path}`. The platform will
  recover contract artifacts from that fallback directory.
- You may create or update project-local `.dockerignore` or `.gitignore` files
  inside `{request.target_project_dir}` if deployment packaging needs to exclude
  caches, virtual environments, logs, secrets, or large local-only artifacts.
- Do not update the platform repository's root `.gitignore` or `.dockerignore`
  unless explicitly requested by the user.
- Do not recursively list dependency/cache directories such as `.venv`,
  `node_modules`, `.pytest_cache`, Playwright caches, Docker build caches,
  `dist`, or `build`.
- Do not remove local cache directories only to make the generated project look
  source-only. Exclude caches from image contexts and reports instead.
- Do not stop broad sets of processes by matching the generated project path.
  Only stop exact process IDs that Deployment started and still owns.
- Do not write QA artifacts under `qa/`; post-deployment validation belongs to
  the QA Agent after Deployment reports target URL(s).
- Do not write handoff artifacts. Handoff owns stakeholder packaging.

Your job:
- Inspect the generated project, requirements, planning artifacts, QA reports,
  Dockerfiles, Docker Compose files, environment examples, README, and generated
  app source.
- Understand the application topology from project evidence, especially Docker
  Compose when present. Do not assume fixed service names, ports, folders, or
  container counts unless the generated project proves them.
- Verify cloud-readiness assumptions for the target runtime before and during
  release: persistence, filesystem behavior, concurrency, networking, ports,
  secrets/configuration, scaling, startup initialization, health checks, and
  restart/revision behavior. Local tests, local Docker smoke checks, and local
  filesystem persistence are useful evidence, but they do not prove the app is
  suitable for the cloud target.
- Decide whether this project can be safely deployed to the configured dev
  environment now.
- If deployable, create only app-owned safe runtime `.env` values needed from
  `.env.example`; never copy OpenAI, Codex, Gemini, Azure, platform, or user
  account secrets into the generated project or deployment artifacts. Build and
  start local containers as needed,
  prepare Azure Container Apps resources, deploy the service or services, and
  report public URL(s).
- If not deployable or a deployment attempt exposes a runtime mismatch, return
  precise evidence and a remediation request for Team Lead. Do not treat an
  application architecture/runtime gap as something Deployment should hide with
  fragile infrastructure workarounds. Classify the likely remediation owner:
  Fullstack for application code, runtime configuration, startup behavior,
  container definition, or persistence support; Deployment for Azure resources,
  registry/auth, secrets, ingress, rollout, scaling, and deployment config.
- Deployment is release-batch by default: deploy the already QA-passed feature
  queue once, not feature-by-feature, unless planning explicitly says otherwise.
- Deployment does not own product correctness. If deployment succeeds, provide
  post-deploy QA target URL(s) so the QA Agent can validate the deployed app.

Safety policy:
- Be careful with cloud resources. Use stable dev names where possible, keep
  names short, and avoid creating many duplicate resources for repeated runs.
- Prefer names such as `rg-agentic-dev`, `agentic-dev-env`, `agenticdevacr`,
  `app-agentic-<service>-dev`, and `agentic-<service>:latest` when they fit the
  actual topology. Adapt only when the generated project requires it.
- Do not delete resource groups, registries, container apps, databases, volumes,
  or user data unless the user explicitly requested teardown.
- Do not run destructive migrations. If a database/schema/data migration is
  needed, block and ask for explicit approval in the deployment report.
- Do not print secret values. Redact secrets in logs, reports, and summaries.
- Do not bake secrets into images. Use runtime environment variables or cloud
  secrets.
- If Azure, Docker, credentials, subscription, quota, or runtime readiness is
  missing, report `blocked` with exact remediation.

Nth release / redeploy policy:
- Assume this may be the 2nd, 3rd, or Nth release for the same dev environment.
- Before creating cloud resources, inspect whether the expected resource group,
  registry, Container Apps environment, container apps, images, secrets, and env
  vars already exist.
- Reuse/update existing dev infrastructure by default. Prefer update/revision
  commands over duplicate create commands when a matching resource exists.
- Create a new resource only when no suitable existing dev resource is present,
  or when the existing resource is incompatible and the report explains why.
- If a generated project adds or removes services compared with the existing
  deployment, extend or update the existing topology intentionally and document
  which resources were reused, updated, created, or left untouched.
- Never create per-run resource names just because this is a new platform run.
- The deployment summary must say whether this was an initial deploy or a
  redeploy/update, and list reused vs created resources.

Non-exhaustive deployment toolbox:
- Use this as guidance, not as a limiting checklist.
- You may inspect Docker Compose, Dockerfiles, application entrypoints, exposed
  ports, health endpoints, env examples, README commands, and QA artifacts.
- Useful tools may include `docker compose config`, `docker compose up --build`,
  `docker compose ps`, `docker compose logs`, `az account show`, `az acr`,
  `az containerapp`, HTTP smoke tests, browser smoke tests, and generated helper
  scripts.
- Choose tools based on the project. Do not run every possible tool mechanically.
- Use long enough timeouts for container builds/deployments. A full Docker build
  or Azure deployment may legitimately need up to 3600 seconds.
- Leave containers/images/volumes in place unless cleanup is explicitly safe and
  requested, so the next deployment run can reuse cache.

Required output contract:
- Write `{result_path}`.
- Write `{plan_json_path}` and `{plan_markdown_path}`.
- Write `{request_json_path}` and `{request_markdown_path}`.
- Write `{summary_markdown_path}`.
- If those exact paths are blocked by sandbox policy, write equivalent files
  under `{fallback_deployment_dir}` and say so explicitly.
- End your final message with exactly one status line:
  `DEPLOYMENT_STATUS: deployed`, `DEPLOYMENT_STATUS: blocked`,
  `DEPLOYMENT_STATUS: failed`, or `DEPLOYMENT_STATUS: unknown`.

The result JSON must be valid JSON and include at least:
```json
{{
  "status": "blocked",
  "target_environment": "azure-container-apps-dev",
  "topology_summary": "what topology you inferred from project files",
  "deployment_targets": [
    {{
      "service": "service name inferred from project",
      "runtime": "container-app",
      "image": "image name if built or planned",
      "public_url": ""
    }}
  ],
  "public_urls": [],
  "post_deploy_qa_targets": [],
  "resource_changes": [
    {{
      "name": "resource name",
      "type": "resource type",
      "action": "reused | updated | created | skipped",
      "reason": "why this action was chosen"
    }}
  ],
  "actions_performed": [
    {{
      "name": "action name",
      "status": "passed",
      "evidence": "what command/evidence supports it"
    }}
  ],
  "remediation_requests": [
    {{
      "owner_agent": "fullstack-agent | deployment-agent",
      "reason": "why this owner should handle the next repair",
      "evidence_refs": ["artifact paths or log locations"],
      "recommended_fix": "concrete repair request for Team Lead to route"
    }}
  ],
  "blockers": [],
  "risks": []
}}
```

If status is `deployed`, `public_urls` and `post_deploy_qa_targets` must include
the deployed public endpoint(s). If status is `blocked`, include blockers and
do not pretend deployment succeeded. If status is `failed`, include the failure
evidence and the safest next step. If status is `unknown`, explain which pieces
of evidence conflict or are missing.

The Markdown artifacts should be operator-readable and explain:
- inferred topology and why;
- deployment strategy and resource naming;
- cloud-readiness assumptions checked and their result;
- remediation owner and concrete repair request when deployment cannot proceed;
- commands/actions performed or intentionally skipped;
- public URL(s), if deployed;
- post-deploy QA target(s), if deployed;
- risks, blockers, and next steps.
{repair_note}
"""


def read_deployment_contract(
    run_dir: Path,
    target_dir: Path,
    summary: str,
) -> dict[str, Any]:
    _recover_misplaced_deployment_contract_artifacts(run_dir, target_dir)
    status = _parse_status(summary)
    required_paths = [
        DEPLOYMENT_PLAN_JSON,
        DEPLOYMENT_PLAN_MARKDOWN,
        DEPLOYMENT_REQUEST_JSON,
        DEPLOYMENT_REQUEST_MARKDOWN,
        DEPLOYMENT_SUMMARY_MARKDOWN,
    ]
    errors: list[str] = []
    optional_artifacts: list[str] = []

    if status is None:
        errors.append(
            "Deployment Codex final message did not include "
            "DEPLOYMENT_STATUS: deployed|blocked|failed|unknown."
        )
    for relative_path in required_paths:
        if not (run_dir / relative_path).exists():
            errors.append(f"Missing required deployment artifact: {relative_path}.")

    payload, payload_errors = _load_best_deployment_result(run_dir, target_dir)
    errors.extend(payload_errors)
    if payload:
        result_status = str(payload.get("status", "")).lower()
        if result_status not in DEPLOYMENT_STATUSES:
            errors.append("Deployment result JSON must include deployed|blocked|failed|unknown.")
        elif status and result_status != status:
            errors.append("Deployment result JSON status does not match final status line.")
        if result_status == "deployed":
            if not _string_list(payload.get("public_urls")):
                errors.append("Deployed result must include public_urls.")
            if not _string_list(payload.get("post_deploy_qa_targets")):
                errors.append("Deployed result must include post_deploy_qa_targets.")

    return {
        "status": status or str(payload.get("status") or "unknown").lower(),
        "contract_valid": not errors,
        "contract_errors": errors,
        "optional_artifacts": optional_artifacts,
        "result": payload,
    }


def _recover_misplaced_deployment_contract_artifacts(run_dir: Path, target_dir: Path) -> None:
    """Recover deployment artifacts when Codex writes inside generated-project."""

    destination_result = run_dir / DEPLOYMENT_RESULT_JSON
    source_result = target_dir / DEPLOYMENT_RESULT_JSON
    if source_result.exists() and not _artifact_is_valid_enough(destination_result):
        _copy_deployment_contract_artifacts(target_dir, run_dir, overwrite=True)
        return

    for relative_path in [
        DEPLOYMENT_RESULT_JSON,
        DEPLOYMENT_PLAN_JSON,
        DEPLOYMENT_PLAN_MARKDOWN,
        DEPLOYMENT_REQUEST_JSON,
        DEPLOYMENT_REQUEST_MARKDOWN,
        DEPLOYMENT_SUMMARY_MARKDOWN,
    ]:
        source = target_dir / relative_path
        destination = run_dir / relative_path
        if not source.exists():
            continue
        if destination.exists() and _artifact_is_valid_enough(destination):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def public_urls_from_deployment_result(run_dir: Path) -> list[str]:
    result_path = run_dir / DEPLOYMENT_RESULT_JSON
    if not result_path.exists():
        return []
    payload, _errors = _load_json_object(result_path)
    if payload is None:
        return []
    return _string_list(payload.get("public_urls"))


def _parse_status(summary: str) -> str | None:
    match = DEPLOYMENT_STATUS_PATTERN.search(summary)
    return match.group(1).lower() if match else None


def _execution_id(request: ExecutionRequest) -> str:
    if request.execution_id:
        return request.execution_id
    return build_agent_execution_id(
        run_id=request.run_id,
        agent_id=DEPLOYMENT_CODEX_AGENT_ID,
        target=request.active_feature.get("id") if request.active_feature else "sprint",
        intent=request.execution_intent or "deployment",
    )


def _write_contract_failure_artifacts(
    run_dir: Path,
    summary: str,
    errors: list[str],
) -> str:
    result_path = run_dir / DEPLOYMENT_RESULT_JSON
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "failed",
        "target_environment": "azure-container-apps-dev",
        "topology_summary": "",
        "deployment_targets": [],
        "public_urls": [],
        "post_deploy_qa_targets": [],
        "actions_performed": [],
        "blockers": ["Deployment Codex did not satisfy the platform output contract."],
        "risks": [],
        "contract_errors": errors,
    }
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = "\n".join(
        [
            "# Deployment Summary",
            "",
            "Status: failed",
            "",
            "The Deployment Codex Agent did not satisfy the required output contract.",
            "",
            "## Contract Errors",
            "",
            *[f"- {error}" for error in errors],
            "",
            "## Last Deployment Codex Message",
            "",
            "```text",
            summary or "(empty)",
            "```",
            "",
        ]
    )
    (run_dir / DEPLOYMENT_SUMMARY_MARKDOWN).write_text(report, encoding="utf-8")
    return f"{report}\nDEPLOYMENT_STATUS: failed\n"


def _deployment_release_context(
    run_dir: Path,
    request: ExecutionRequest,
) -> dict[str, list[str]]:
    """Return release-scope facts from graph state, falling back to the request.

    The execution request is rewritten feature-by-feature by the Fullstack Agent,
    so by deployment time it may describe the last feature run rather than the
    whole QA-passed release batch. The delivery state is the orchestration source
    of truth for release-batch deployment.
    """

    state_path = run_dir / DELIVERY_STATE_SNAPSHOT
    completed = list(request.completed_feature_ids)
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            state = {}
        if isinstance(state, dict):
            state_completed = _string_list(state.get("completed_feature_ids"))
            if state_completed:
                completed = state_completed
    release_scope = completed or list(request.completed_feature_ids)
    return {
        "completed_feature_ids": completed,
        "release_scope": release_scope,
    }


def _load_best_deployment_result(
    run_dir: Path,
    target_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Load the best available deployment result contract.

    Prefer a valid run-level contract. If the run-level contract is invalid but
    Codex wrote a valid fallback inside the generated project, recover and use
    the fallback. This is the reconciliation layer between agent-written
    artifacts and platform state.
    """

    run_result = run_dir / DEPLOYMENT_RESULT_JSON
    fallback_result = target_dir / DEPLOYMENT_RESULT_JSON
    run_payload, run_errors = _load_json_object(run_result)
    if run_payload is not None:
        return run_payload, []

    fallback_payload, fallback_errors = _load_json_object(fallback_result)
    if fallback_payload is not None:
        _copy_deployment_contract_artifacts(target_dir, run_dir, overwrite=True)
        return fallback_payload, []

    if run_result.exists():
        return {}, [f"Deployment result JSON is invalid: {'; '.join(run_errors)}."]
    if fallback_result.exists():
        return {}, [f"Fallback deployment result JSON is invalid: {'; '.join(fallback_errors)}."]
    return {}, [f"Missing required deployment result JSON: {DEPLOYMENT_RESULT_JSON}."]


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"{path} does not exist"]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return None, [str(exc)]
    if not isinstance(loaded, dict):
        return None, ["JSON document must be an object"]
    return loaded, []


def _artifact_is_valid_enough(path: Path) -> bool:
    if path.suffix.lower() != ".json":
        return path.exists() and path.stat().st_size > 0
    payload, _errors = _load_json_object(path)
    return payload is not None


def _copy_deployment_contract_artifacts(
    source_root: Path, destination_root: Path, *, overwrite: bool
) -> None:
    for relative_path in [
        DEPLOYMENT_RESULT_JSON,
        DEPLOYMENT_PLAN_JSON,
        DEPLOYMENT_PLAN_MARKDOWN,
        DEPLOYMENT_REQUEST_JSON,
        DEPLOYMENT_REQUEST_MARKDOWN,
        DEPLOYMENT_SUMMARY_MARKDOWN,
    ]:
        source = _deployment_artifact_source(source_root, relative_path)
        destination = destination_root / relative_path
        if source is None or (destination.exists() and not overwrite):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _deployment_artifact_source(source_root: Path, relative_path: str) -> Path | None:
    candidates = [
        source_root / relative_path,
        source_root / "deployment" / Path(relative_path).name,
    ]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _unique_artifacts(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique
