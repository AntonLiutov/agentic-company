"""Codex CLI runner for the Architect agent."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_company.agents.architecture.graph import (
    ARCHITECT_AGENT_ID,
    ARCHITECTURE_JSON,
    ARCHITECTURE_MD,
    ARCHITECTURE_MMD,
    ARCHITECTURE_REQUEST,
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
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
ARCHITECT_WORK_DIR = Path("upstream-planning") / "architect"
PROMPT_PREVIEW_CHARS = 2500

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class ArchitectCodexRunner:
    """Run architecture planning as a scoped Codex artifact-writing task."""

    codex_binary: str | None = None
    sandbox: str = "workspace-write"
    timeout_seconds: int = 1800
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = _load_request(run_dir)
        execution_id = build_agent_execution_id(
            run_id=str(request["run_id"]),
            agent_id=ARCHITECT_AGENT_ID,
            correlation_id="architecture",
            intent="solution_architecture",
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=ARCHITECT_AGENT_ID,
        )
        artifact_dir = execution_artifact_dir(
            root=run_dir / ARCHITECT_WORK_DIR / "codex",
            execution_id=execution_id,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary_path = artifact_dir / "summary.md"
        prompt_path = artifact_dir / "prompt.md"
        log_path = artifact_dir / "execution.log"
        raw_events_path = artifact_dir / "events.jsonl"
        prompt = build_architecture_codex_prompt(request, run_dir)
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
            f"agent_id={ARCHITECT_AGENT_ID}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n\n"
            "Architect Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir,
            str(request["run_id"]),
            ARCHITECT_AGENT_ID,
            "architecture_codex_started",
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
                agent_id=ARCHITECT_AGENT_ID,
                work_item_id="PLAN-02",
            )
        except FileNotFoundError:
            LOGGER.exception("Architect Codex CLI missing run_id=%s", request["run_id"])
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
            "architecture_completed"
            if completed.returncode == 0 and not contract_errors
            else "architecture_failed"
        )
        if contract_errors:
            summary = (
                summary.rstrip()
                + "\n\nContract errors:\n"
                + "\n".join(f"- {error}" for error in contract_errors)
            )
            summary_path.write_text(summary + "\n", encoding="utf-8")

        output_artifacts = [
            ARCHITECTURE_MD,
            ARCHITECTURE_JSON,
            ARCHITECTURE_MMD,
            summary_path.relative_to(run_dir).as_posix(),
            prompt_path.relative_to(run_dir).as_posix(),
            log_path.relative_to(run_dir).as_posix(),
            *structured_artifacts,
        ]
        write_event(
            run_dir,
            str(request["run_id"]),
            ARCHITECT_AGENT_ID,
            "architecture_codex_completed",
            {
                "status": status,
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=ARCHITECT_AGENT_ID,
            status=status,
            output_artifacts=_existing_artifacts(run_dir, output_artifacts),
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=contract_errors,
            recommended_next_action=(
                "Proceed to project management planning."
                if status == "architecture_completed"
                else "Inspect Architect Codex artifacts and retry architecture planning."
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


def build_architecture_codex_prompt(request: dict[str, Any], run_dir: Path) -> str:
    input_artifacts = [
        str(request.get("requirements_artifact") or "00-requirements.md"),
        BUSINESS_ANALYSIS_MD,
        BUSINESS_ANALYSIS_JSON,
    ]
    artifact_previews = _render_artifact_previews(run_dir, input_artifacts)
    available_agents = _render_available_agents(request.get("available_agents"))
    incoming_messages = str(request.get("incoming_messages") or "").strip()
    live_messages = render_incoming_messages_for_prompt(
        run_dir,
        to_agent=ARCHITECT_AGENT_ID,
        limit=6,
    )
    return f"""You are the Solution Architect Agent for agentic-company.

Your role follows solution architecture practice: translate business analysis
into a technical architecture, component boundaries, state/data direction,
quality attributes, implementation constraints, deployment implications,
technical decisions, tradeoffs, risks, and a readable solution diagram.
You are not the Business Analyst, Project Manager, Team Lead, or developer.

Architecture principles:
- Preserve product intent from the BA artifacts. Do not invent product scope.
- Define the smallest architecture that satisfies the requirements and platform
  constraints. Avoid enterprise ceremony for small prototypes.
- Scale architecture detail to the source complexity. A simple internal demo app
  should get a simple deployable architecture with enough clarity for Team Lead,
  Fullstack, QA, and Deployment to execute; a complex product can justify deeper
  tradeoffs and more artifacts.
- Record assumptions and open questions instead of silently deciding unresolved
  product issues.
- Use quality attributes where relevant: reliability, security, performance,
  cost awareness, operational visibility, maintainability, and usability.
- Prefer explicit technical decisions and rejected options over vague guidance.
- Treat application deployment as the normal delivery path unless the source
  explicitly excludes deployment. Do not design a local-only system by default
  for an app, site, API, service, or automation that a user expects to use.
- Do not create sprint plans, planned work item contracts, delivery sequencing, or code.

Platform context:
- The current platform path uses Azure-oriented deployment infrastructure.
- Azure deployment is a supported platform capability, not speculative future
  scope. When requirements ask for Azure/dev deployment or a deployed URL, model
  it as a real deployment topology and release gate unless the source explicitly
  excludes deployment. Unknown resource names, credentials, registry, ingress, or
  QA-gate details are deployment inputs for the Deployment Agent to inspect
  later; do not mark Azure deployment impossible or future-only by default. If
  platform context indicates Azure integration is available, assume the
  Deployment Agent can inspect or create suitable dev resources unless a source
  constraint forbids it.
- The current AI provider path is OpenAI/Codex.
- Generated product code will live inside a run-local generated project.
- Head Agent coordinates this planning flow. Treat incoming coordinator
  messages as assignment context, answer back through your final summary, and
  keep artifacts as the source of truth.
- Do not coordinate directly with Business Analyst, PM, Team Lead, or delivery
  agents. Head Agent owns routing.
- Treat platform execution details as internal coordination context unless they
  affect the product/system architecture. Examples: write policy, allowed
  artifact paths, agent registry, current AI provider, and orchestration routing
  belong in JSON `coordination_notes`, not in architecture constraints or the
  client-readable Markdown.

Available agent registry snapshot:
{available_agents}

Use the registry snapshot only as context for internal JSON `coordination_notes`.
Do not treat it as an exhaustive future limit. Do not copy registry agents into
the architecture Markdown as stakeholders or as a fixed process.

Run workspace:
{run_dir}

Incoming coordinator messages:
{incoming_messages or "- No incoming coordinator messages were provided."}

Latest live messages for this agent:
{live_messages}

Input artifacts:
- {input_artifacts[0]}
- {BUSINESS_ANALYSIS_MD}
- {BUSINESS_ANALYSIS_JSON}

Source loading policy:
- The prompt includes short previews only to protect the context window.
- Treat artifact paths as the source of truth. Open and inspect full files from
  the run workspace when details matter.
- Do not paste whole upstream artifacts into your outputs. Summarize and preserve
  concise source_refs instead.

Allowed writes:
- {ARCHITECTURE_MD}
- {ARCHITECTURE_JSON}
- {ARCHITECTURE_MMD}

Write policy:
- Write only the three allowed architecture artifacts listed above.
- Do not modify BA artifacts, generated-project files, implementation, QA,
  deployment, handoff, Team Lead, or PM artifacts.
- Do not edit platform repository files.
- Do not print secrets.

Architecture output:
- Mermaid is the primary visual architecture artifact. Use it to make the
  solution shape understandable before someone reads the full brief or JSON.
- Markdown is a concise technical architecture brief for human reviewers and
  downstream technical planning. Start from the solution shape and diagram
  orientation, then summarize service boundaries, data/state direction,
  deployment topology, quality attributes, key decisions, risks, assumptions,
  and open questions. Do not duplicate the full JSON contract in Markdown.
- JSON is the internal platform contract for PM, Head Agent, Team Lead,
  Fullstack, QA, Deployment, and future agents. Put exhaustive downstream
  constraints, coordination notes, QA/deployment implications, and traceability
  here instead of overloading the Markdown. Planning constraints for PM are
  useful; sprint plans, planned work item contracts, and delivery sequencing are not.
- Write valid Mermaid text to `{ARCHITECTURE_MMD}`. Prefer `flowchart` or
  `graph`. Show user/client surface, services/components, state/data ownership,
  local runtime, and deployment target when applicable.
- Optimize the Mermaid diagram for communication, not traceability. It should be
  readable at thumbnail size and understandable to a non-implementer.
- Keep node labels short, usually 2-4 words. Prefer domain names like
  "User", "Web UI", "API", "Database", "Queue", "Worker", "Mobile App",
  "External System", or similarly compact labels derived from the actual
  product. Avoid sentence labels inside nodes.
- Avoid unexplained acronyms, abbreviations, internal code names, or provider
  shorthand in diagram labels. Prefer business-readable labels. If a technical
  acronym is essential, expand it in the same label or move the provider-specific
  detail to Markdown and JSON.
- Keep edge labels short, usually 0-3 words. Omit labels when the connection is
  obvious. Do not place implementation verbs or acceptance-criteria text on
  edges, such as long create/list/update phrases. Put those details in Markdown
  and JSON instead.
- Draw only product/system architecture and required runtime/deployment
  boundaries. Do not include delivery workflow, QA process, CI/CD pipeline,
  approval gates, agent workflow, platform orchestration, legends, or process
  controls unless the user explicitly asked for those as part of the product
  architecture.
- Use arrows only for meaningful runtime calls, data flows, dependencies, or
  ownership relationships. Do not invent runtime calls. If a deployment
  environment is relevant, show it as a boundary/environment rather than as a
  deployment process.
- Put process notes, QA implications, deployment constraints, traceability,
  and operational caveats in Markdown and JSON, not inside the diagram.
- In JSON, produce a structured object with these top-level keys:
  architecture_goal, system_context, components, service_boundaries,
  data_model_direction, api_contract_direction, deployment_topology,
  provided_constraints, quality_attributes, technical_decisions,
  rejected_options, implementation_constraints, qa_implications,
  deployment_implications, risks, open_questions, coordination_notes, diagram.
- `provided_constraints` must contain constraints that affect the generated
  product/system architecture. Do not include tool write policy, allowed
  artifact paths, agent registry, orchestration routing, or AI-provider details
  used only to run this platform. Put those internal details in
  `coordination_notes` only when downstream agents need them.
- Preserve source references from BA JSON as `source_refs` on related JSON
  decisions, constraints, risks, QA/deployment implications, and open questions.
- Preserve every distinct feature/source label from BA and requirements. Do not
  collapse many features into a smaller fixed set, and do not invent generic
  work item ids when the user provided specific labels.
- If BA marked a question unresolved, either carry it forward as an open
  question or make a clearly labeled technical assumption with source refs.
- Do not overrule BA non-goals. If a technical concern conflicts with a non-goal,
  record the tradeoff and risk.

Input artifact previews:
{artifact_previews}

When finished, summarize the artifacts you wrote and the highest-risk technical
decisions or open questions. Do not ask the user for permission to continue.
"""


def _load_request(run_dir: Path) -> dict[str, Any]:
    request_path = run_dir / ARCHITECTURE_REQUEST
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


def _render_artifact_previews(run_dir: Path, artifacts: list[str]) -> str:
    blocks: list[str] = []
    for artifact in artifacts:
        path = Path(artifact)
        resolved = path if path.is_absolute() else run_dir / path
        blocks.append(f"### {artifact}\n```text\n{_artifact_preview(resolved)}\n```")
    return "\n\n".join(blocks)


def _artifact_preview(path: Path, limit: int = PROMPT_PREVIEW_CHARS) -> str:
    if not path.exists():
        return f"Missing artifact: {path}"
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
    return completed.stdout.strip() or "Architect Codex completed without stdout."


def _contract_errors(run_dir: Path) -> list[str]:
    errors: list[str] = []
    markdown_path = run_dir / ARCHITECTURE_MD
    json_path = run_dir / ARCHITECTURE_JSON
    mermaid_path = run_dir / ARCHITECTURE_MMD
    if not markdown_path.exists():
        errors.append(f"Missing required artifact: {ARCHITECTURE_MD}")
    if not mermaid_path.exists():
        errors.append(f"Missing required artifact: {ARCHITECTURE_MMD}")
    elif not _looks_like_mermaid(read_text_artifact(mermaid_path)):
        errors.append(f"{ARCHITECTURE_MMD} does not look like a Mermaid graph.")
    if not json_path.exists():
        errors.append(f"Missing required artifact: {ARCHITECTURE_JSON}")
        return errors
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{ARCHITECTURE_JSON} is not valid JSON: {exc}")
        return errors
    required = {
        "architecture_goal",
        "system_context",
        "components",
        "service_boundaries",
        "data_model_direction",
        "api_contract_direction",
        "deployment_topology",
        "provided_constraints",
        "quality_attributes",
        "technical_decisions",
        "rejected_options",
        "implementation_constraints",
        "qa_implications",
        "deployment_implications",
        "risks",
        "open_questions",
        "coordination_notes",
        "diagram",
    }
    missing = (
        sorted(required.difference(payload)) if isinstance(payload, dict) else sorted(required)
    )
    errors.extend(f"Missing required JSON key: {key}" for key in missing)
    return errors


def _looks_like_mermaid(text: str) -> bool:
    stripped = text.strip().lower()
    return stripped.startswith(("flowchart", "graph", "sequencediagram", "statediagram"))


def _existing_artifacts(run_dir: Path, artifacts: list[str]) -> list[str]:
    existing: list[str] = []
    for artifact in artifacts:
        if artifact not in existing and (run_dir / artifact).exists():
            existing.append(artifact)
    return existing
