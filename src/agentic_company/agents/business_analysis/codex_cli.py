"""Codex CLI runner for the Business Analyst agent."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_company.agents.business_analysis.graph import (
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
    BUSINESS_ANALYSIS_REQUEST,
    BUSINESS_ANALYST_AGENT_ID,
)
from agentic_company.integrations.codex import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts.artifacts import read_text_artifact
from agentic_company.platform.db.models import AgentRunResult
from agentic_company.platform.mirror.messages import render_incoming_messages_for_prompt
from agentic_company.platform.run.events import write_event
from agentic_company.platform.run.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)

LOGGER = logging.getLogger(__name__)
BUSINESS_ANALYST_WORK_DIR = Path("upstream-planning") / "business-analyst"
PROMPT_PREVIEW_CHARS = 4000

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class BusinessAnalystCodexRunner:
    """Run business analysis as a scoped Codex artifact-writing task."""

    codex_binary: str | None = None
    sandbox: str = "workspace-write"
    timeout_seconds: int = 1800
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = _load_request(run_dir)
        execution_id = build_agent_execution_id(
            run_id=str(request["run_id"]),
            agent_id=BUSINESS_ANALYST_AGENT_ID,
            correlation_id="requirements",
            intent="business_analysis",
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=BUSINESS_ANALYST_AGENT_ID,
        )
        artifact_dir = execution_artifact_dir(
            root=run_dir / BUSINESS_ANALYST_WORK_DIR / "codex",
            execution_id=execution_id,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary_path = artifact_dir / "summary.md"
        prompt_path = artifact_dir / "prompt.md"
        log_path = artifact_dir / "execution.log"
        raw_events_path = artifact_dir / "events.jsonl"
        prompt = build_business_analysis_codex_prompt(request, run_dir)
        command = build_codex_exec_command(
            codex_binary=self.codex_binary,
            model=str(request["model"]),
            sandbox=self.sandbox,
            target_project_dir=str(run_dir),
            run_dir=run_dir,
            summary_path=summary_path,
            force_sandbox=True,
            resume_session_id=str(request.get("codex_resume_thread_id") or ""),
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        log_path.write_text(
            f"$ {' '.join(command)}\n"
            f"timeout_seconds={self.timeout_seconds}\n"
            f"agent_id={BUSINESS_ANALYST_AGENT_ID}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n\n"
            "Business Analyst Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir,
            str(request["run_id"]),
            BUSINESS_ANALYST_AGENT_ID,
            "business_analysis_codex_started",
            {"execution_id": execution_id, "codex_execution_id": codex_execution_id},
        )
        try:
            completed = self._execute(
                command,
                prompt,
                log_path,
                raw_events_path,
                codex_execution_id=codex_execution_id,
                run_dir=run_dir,
                run_id=str(request["run_id"]),
                agent_id=BUSINESS_ANALYST_AGENT_ID,
                work_item_id="PLAN-01",
            )
        except FileNotFoundError:
            LOGGER.exception("Business Analyst Codex CLI missing run_id=%s", request["run_id"])
            summary_path.write_text("Codex CLI was not found.\n", encoding="utf-8")
            completed = subprocess.CompletedProcess(command, 1, stdout="", stderr="")

        structured_artifacts = write_structured_codex_artifacts(
            run_dir,
            completed.stdout,
            raw_events_filename=raw_events_path.relative_to(run_dir).as_posix(),
        )
        summary = _summary_text(summary_path, completed)
        codex_thread_id = extract_codex_thread_id(raw_events_path) or str(
            request.get("codex_resume_thread_id") or ""
        )
        contract_errors = _contract_errors(run_dir)
        status = (
            "business_analysis_completed"
            if completed.returncode == 0 and not contract_errors
            else "business_analysis_failed"
        )
        if contract_errors:
            summary = (
                summary.rstrip()
                + "\n\nContract errors:\n"
                + "\n".join(f"- {error}" for error in contract_errors)
            )
            summary_path.write_text(summary + "\n", encoding="utf-8")

        output_artifacts = [
            BUSINESS_ANALYSIS_MD,
            BUSINESS_ANALYSIS_JSON,
            summary_path.relative_to(run_dir).as_posix(),
            prompt_path.relative_to(run_dir).as_posix(),
            log_path.relative_to(run_dir).as_posix(),
            *structured_artifacts,
        ]
        write_event(
            run_dir,
            str(request["run_id"]),
            BUSINESS_ANALYST_AGENT_ID,
            "business_analysis_codex_completed",
            {
                "status": status,
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=BUSINESS_ANALYST_AGENT_ID,
            status=status,
            output_artifacts=_existing_artifacts(run_dir, output_artifacts),
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=contract_errors,
            recommended_next_action=(
                "Proceed to architecture planning."
                if status == "business_analysis_completed"
                else "Inspect Business Analyst Codex artifacts and retry analysis."
            ),
        )

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


def build_business_analysis_codex_prompt(request: dict[str, Any], run_dir: Path) -> str:
    requirements_artifact = str(request["requirements_artifact"])
    requirements_path = _artifact_path(run_dir, requirements_artifact)
    requirements_preview = _artifact_preview(requirements_path)
    available_agents = _render_available_agents(request.get("available_agents"))
    incoming_messages = str(request.get("incoming_messages") or "").strip()
    live_messages = render_incoming_messages_for_prompt(
        run_dir,
        to_agent=BUSINESS_ANALYST_AGENT_ID,
        limit=6,
    )
    return f"""You are the Business Analyst Agent for agentic-company.

Your role follows business analysis practice: discover the business need, users,
scope, business rules, risks, assumptions, open questions, and testable
acceptance criteria. You are not the Architect, Project Manager, Team Lead, or
developer.

Platform context:
- The current platform path uses Azure-oriented deployment infrastructure.
- Azure deployment is a supported platform capability in this delivery system,
  not speculative future scope. If the user's requirements mention Azure/dev
  deployment, stable dev resource updates, or a deployed URL, capture that as a
  real business acceptance/deployment expectation. Unknown resource names,
  credentials, registry, ingress, or QA-gate details should become open
  questions or risks for downstream delivery, not a reason to remove or reject
  the deployment expectation.
- The current AI provider path is OpenAI/Codex.
- Preserve the user's product intent and prepare clear business analysis for
  downstream agents.
- Head Agent coordinates this planning flow. Treat incoming coordinator
  messages as assignment context, answer back through your final summary, and
  keep artifacts as the source of truth.
- Do not coordinate directly with Architect, Project Manager, Team Lead, or
  delivery agents. Head Agent owns routing.
- If requirements conflict with the Azure/OpenAI platform context, record that
  as an open question, assumption, or risk. Do not reject the requirements.
- Treat platform execution details as internal coordination context unless the
  user explicitly made them product requirements. Examples: write policy,
  allowed artifact paths, agent registry, current AI provider, and orchestration
  routing belong in JSON `coordination_notes`, not in user-facing Markdown or
  product-facing `provided_constraints`.

Complexity and delivery calibration:
- Scale the depth of analysis to the source request. A simple demo app needs a
  compact, clear BA brief; a complex regulated product needs deeper rules,
  risks, and open questions. Do not inflate a simple request into an enterprise
  program.
- Preserve minimum BA standards even when the request is simple: goal, target
  users, core user journeys, acceptance criteria, business rules, scope,
  non-goals, assumptions, risks, open questions, and source traceability.
- For app, site, API, service, or automation requests, treat deployable access
  as the default delivery expectation unless the user explicitly says local
  only, prototype only, no deployment, or similar. Capture deployment as a
  business expectation or acceptance constraint, not as an optional future idea.
- Do not add authentication, persistence, permissions, analytics, mobile apps,
  integrations, compliance, or enterprise workflow unless the source asks for
  them or they are genuinely required to satisfy the stated product goal.

Available agent registry snapshot:
{available_agents}

Use the registry snapshot as context about current platform roles. Do not treat
it as an exhaustive future limit; new agents may be added without changing this
Business Analyst contract.
Do not copy registry agents into target users, business stakeholders, or
user-facing stakeholder brief sections. Target users and stakeholders are
people, customer roles, product owners, business owners, reviewers, operators,
or organizations from the product context. If an internal platform role needs a
note, put it only in JSON `coordination_notes`.

Run workspace:
{run_dir}

Incoming coordinator messages:
{incoming_messages or "- No incoming coordinator messages were provided."}

Latest live messages for this agent:
{live_messages}

Requirements artifact:
- {requirements_artifact}

Source loading policy:
- The prompt includes only a short preview to protect the context window.
- The full requirements file is available in the run workspace. Open and inspect
  it directly when you need complete details or traceability.
- Do not paste the full source file into your response or generated JSON. Preserve
  source labels and concise references instead.

Allowed writes:
- {BUSINESS_ANALYSIS_MD}
- {BUSINESS_ANALYSIS_JSON}

Write policy:
- Write only the two allowed business analysis artifacts listed above.
- Do not modify generated-project files.
- Do not write implementation, QA, deployment, handoff, or Team Lead artifacts.
- Do not create sprint plans, planned work item contracts, delivery sequencing, or technical
  architecture.
- Do not edit platform repository files.
- Do not print secrets.

Business analysis output:
- Produce two artifacts with different audiences.
- Markdown is the user-facing business analysis brief for the user/product
  owner. Keep it focused on product intent, business scope, users, business
  stakeholders, rules, acceptance criteria, assumptions, risks, and open
  questions. Do not mention internal platform agents, agent registry details,
  orchestration, or implementation-agent routing in Markdown.
- JSON is the internal platform contract for Head Agent and downstream agents.
  It may include registry-aware `coordination_notes`, but those notes must stay
  out of the user-facing Markdown.
- In JSON, produce a structured object with these top-level keys:
  product_goal, target_users, stakeholders, user_stories, acceptance_criteria,
  business_rules, scope, non_goals, provided_constraints, assumptions, risks,
  open_questions, open_question_triage, recommended_product_decisions,
  coordination_notes, delivery_notes.
- Treat the JSON as an internal contract for downstream platform roles from the
  registry snapshot. Do not treat Markdown as that internal contract.
- Acceptance criteria must be business-facing, testable, and scoped.
- If the requirements include work item ids, sprint ids, milestones, phases, or
  named plan markers, preserve those original labels as `source_refs` on related
  JSON user stories, acceptance criteria, risks, and open questions.
- Preserve every distinct feature/source label from the requirements. Do not
  collapse many features into a smaller fixed set, and do not invent generic
  work item ids when the user provided specific labels.
- Preserve source references for both feature and non-feature requirements.
  Use the most specific original label available: feature id, milestone, phase,
  section heading, bullet label, requirement name, or a concise descriptive
  label derived from the user's wording when no explicit label exists. Do not
  limit references to examples.
- `provided_constraints` must record constraints that affect the product being
  requested, such as user-provided stack, hosting, compliance, environment, or
  business limitations. Do not include tool write policy, allowed artifact
  paths, agent registry, orchestration routing, or AI-provider details used only
  to run this platform. Put those internal details in `coordination_notes` only
  when downstream agents need them.
- `open_question_triage` should group questions by why they matter, not by a
  hard-coded process or a fixed agent list. Choose category names from the
  substance of the current requirements so future agents can be added without
  changing this contract. Example categories may include architecture_relevant,
  planning_relevant, implementation_relevant, qa_relevant, deployment_relevant,
  business_relevant, security_relevant, or can_defer, but these are examples,
  not an allowed list.
- `coordination_notes` should be short role-relevance notes for
  orchestration. Do not call them handoffs, do not route work, and do not decide
  the next agent; Head Agent owns routing.
- `recommended_product_decisions` may include BA-level recommendations, but each
  item must say whether product-owner confirmation is required. Do not make
  architecture decisions, sprint decisions, or implementation decisions.
- Do not invent sprint assignments. Preserve sprint or milestone labels only
  when the user already provided them; Project Manager owns sprint planning.
- Keep assumptions, risks, and open questions clearly separated; do not silently
  decide unresolved product questions for the user.
- If the requirements are incomplete, write open questions but still provide the
  best bounded draft from the available information.
- Keep the output suitable for a later Architect and Project Manager.

Requirements preview:
```markdown
{requirements_preview}
```

When finished, summarize the artifacts you wrote and the highest-risk open
questions. Do not ask the user for permission to continue.
"""


def _load_request(run_dir: Path) -> dict[str, Any]:
    request_path = run_dir / BUSINESS_ANALYSIS_REQUEST
    return json.loads(request_path.read_text(encoding="utf-8"))


def _render_available_agents(raw_agents: Any) -> str:
    if not isinstance(raw_agents, list) or not raw_agents:
        return "- No active agent registry snapshot was provided."

    lines: list[str] = []
    for raw_agent in raw_agents:
        if not isinstance(raw_agent, dict):
            continue
        agent_id = str(raw_agent.get("agent_id") or "").strip()
        name = str(raw_agent.get("name") or agent_id).strip()
        stage = str(raw_agent.get("stage") or "").strip()
        family = str(raw_agent.get("family") or "").strip()
        runtime = str(raw_agent.get("runtime") or "").strip()
        if not agent_id:
            continue
        details = ", ".join(
            part for part in (f"stage={stage}", f"family={family}", runtime) if part
        )
        lines.append(f"- {agent_id}: {name}" + (f" ({details})" if details else ""))
    return "\n".join(lines) if lines else "- No active agent registry snapshot was provided."


def _artifact_path(run_dir: Path, artifact: str) -> Path:
    path = Path(artifact)
    return path if path.is_absolute() else run_dir / path


def _artifact_preview(path: Path, limit: int = PROMPT_PREVIEW_CHARS) -> str:
    if not path.exists():
        return f"- Missing artifact: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return (
        f"{text[:limit].rstrip()}\n\n... [truncated {omitted} chars; open {path} for full source]"
    )


def _summary_text(
    summary_path: Path,
    completed: subprocess.CompletedProcess[str],
) -> str:
    if summary_path.exists():
        return read_text_artifact(summary_path)
    return completed.stdout.strip() or "Business Analyst Codex completed without stdout."


def _contract_errors(run_dir: Path) -> list[str]:
    errors: list[str] = []
    markdown_path = run_dir / BUSINESS_ANALYSIS_MD
    json_path = run_dir / BUSINESS_ANALYSIS_JSON
    if not markdown_path.exists():
        errors.append(f"Missing required artifact: {BUSINESS_ANALYSIS_MD}")
    if not json_path.exists():
        errors.append(f"Missing required artifact: {BUSINESS_ANALYSIS_JSON}")
        return errors
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{BUSINESS_ANALYSIS_JSON} is not valid JSON: {exc}")
        return errors
    required = {
        "product_goal",
        "target_users",
        "stakeholders",
        "user_stories",
        "acceptance_criteria",
        "business_rules",
        "scope",
        "non_goals",
        "provided_constraints",
        "assumptions",
        "risks",
        "open_questions",
        "open_question_triage",
        "recommended_product_decisions",
        "coordination_notes",
        "delivery_notes",
    }
    missing = (
        sorted(required.difference(payload)) if isinstance(payload, dict) else sorted(required)
    )
    errors.extend(f"Missing required JSON key: {key}" for key in missing)
    return errors


def _existing_artifacts(run_dir: Path, artifacts: list[str]) -> list[str]:
    existing: list[str] = []
    for artifact in artifacts:
        if artifact not in existing and (run_dir / artifact).exists():
            existing.append(artifact)
    return existing
