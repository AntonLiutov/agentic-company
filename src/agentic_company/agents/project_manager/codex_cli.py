"""Codex CLI runner for the Project Manager agent."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_company.agents.project_manager.graph import (
    ARCHITECTURE_JSON,
    ARCHITECTURE_MD,
    ARCHITECTURE_MMD,
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
    PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON,
    PROJECT_MANAGEMENT_JSON,
    PROJECT_MANAGEMENT_MD,
    PROJECT_MANAGEMENT_REQUEST,
    PROJECT_MANAGEMENT_RISKS_MD,
    PROJECT_MANAGEMENT_ROADMAP_CSV,
    PROJECT_MANAGER_AGENT_ID,
)
from agentic_company.integrations.codex import (
    build_codex_exec_command,
    stream_codex_exec_to_log,
    write_structured_codex_artifacts,
)
from agentic_company.platform.artifacts import read_json_artifact, read_text_artifact
from agentic_company.platform.events import write_event
from agentic_company.platform.executions import (
    build_agent_execution_id,
    build_codex_execution_id,
    execution_artifact_dir,
    extract_codex_thread_id,
)
from agentic_company.platform.messages import render_incoming_messages_for_prompt
from agentic_company.platform.models import AgentRunResult

LOGGER = logging.getLogger(__name__)
PROJECT_MANAGER_WORK_DIR = Path("upstream-planning") / "project-manager"
PROMPT_PREVIEW_CHARS = 2000

CommandExecutor = Callable[
    [Sequence[str], str, int, Path, Path],
    subprocess.CompletedProcess[str],
]


@dataclass(slots=True)
class ProjectManagerCodexRunner:
    """Run project management planning as a scoped Codex artifact-writing task."""

    codex_binary: str | None = None
    sandbox: str = "workspace-write"
    timeout_seconds: int = 1800
    command_executor: CommandExecutor | None = None

    def run(self, run_dir: Path) -> AgentRunResult:
        request = _load_request(run_dir)
        execution_id = build_agent_execution_id(
            run_id=str(request["run_id"]),
            agent_id=PROJECT_MANAGER_AGENT_ID,
            target="project-management",
            intent="release_sprint_planning",
        )
        codex_execution_id = build_codex_execution_id(
            execution_id=execution_id,
            codex_agent_id=PROJECT_MANAGER_AGENT_ID,
        )
        artifact_dir = execution_artifact_dir(
            root=run_dir / PROJECT_MANAGER_WORK_DIR / "codex",
            execution_id=execution_id,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary_path = artifact_dir / "summary.md"
        prompt_path = artifact_dir / "prompt.md"
        log_path = artifact_dir / "execution.log"
        raw_events_path = artifact_dir / "events.jsonl"
        prompt = build_project_management_codex_prompt(request, run_dir)
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
            f"agent_id={PROJECT_MANAGER_AGENT_ID}\n"
            f"execution_id={execution_id}\n"
            f"codex_execution_id={codex_execution_id}\n\n"
            "Project Manager Codex execution is starting...\n",
            encoding="utf-8",
        )
        write_event(
            run_dir / "events.jsonl",
            str(request["run_id"]),
            PROJECT_MANAGER_AGENT_ID,
            "project_management_codex_started",
            {"execution_id": execution_id, "codex_execution_id": codex_execution_id},
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
            LOGGER.exception("Project Manager Codex CLI missing run_id=%s", request["run_id"])
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
            "project_management_completed"
            if completed.returncode == 0 and not contract_errors
            else "project_management_failed"
        )
        if contract_errors:
            summary = (
                summary.rstrip()
                + "\n\nContract errors:\n"
                + "\n".join(f"- {error}" for error in contract_errors)
            )
            summary_path.write_text(summary + "\n", encoding="utf-8")

        output_artifacts = [
            PROJECT_MANAGEMENT_MD,
            PROJECT_MANAGEMENT_JSON,
            PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON,
            PROJECT_MANAGEMENT_RISKS_MD,
            PROJECT_MANAGEMENT_ROADMAP_CSV,
            *_sprint_plan_artifacts(run_dir),
            summary_path.relative_to(run_dir).as_posix(),
            prompt_path.relative_to(run_dir).as_posix(),
            log_path.relative_to(run_dir).as_posix(),
            *structured_artifacts,
        ]
        write_event(
            run_dir / "events.jsonl",
            str(request["run_id"]),
            PROJECT_MANAGER_AGENT_ID,
            "project_management_codex_completed",
            {
                "status": status,
                "execution_id": execution_id,
                "codex_execution_id": codex_execution_id,
                "codex_thread_id": codex_thread_id,
            },
        )
        return AgentRunResult(
            agent_id=PROJECT_MANAGER_AGENT_ID,
            status=status,
            output_artifacts=_existing_artifacts(run_dir, output_artifacts),
            summary=summary,
            execution_id=execution_id,
            codex_thread_id=codex_thread_id,
            blocking_findings=contract_errors,
            recommended_next_action=(
                "Review PM sprint plans, then connect a selected sprint to Team Lead."
                if status == "project_management_completed"
                else "Inspect Project Manager Codex artifacts and retry project planning."
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


def build_project_management_codex_prompt(request: dict[str, Any], run_dir: Path) -> str:
    input_artifacts = [
        str(request.get("requirements_artifact") or "00-requirements.md"),
        BUSINESS_ANALYSIS_MD,
        BUSINESS_ANALYSIS_JSON,
        ARCHITECTURE_MD,
        ARCHITECTURE_JSON,
        ARCHITECTURE_MMD,
    ]
    artifact_previews = _render_artifact_previews(run_dir, input_artifacts)
    available_agents = _render_available_agents(request.get("available_agents"))
    incoming_messages = str(request.get("incoming_messages") or "").strip()
    live_messages = render_incoming_messages_for_prompt(
        run_dir,
        to_agent=PROJECT_MANAGER_AGENT_ID,
        limit=6,
    )
    planning_policy = request.get("planning_policy") or {}
    return f"""You are the Project Manager Agent for agentic-company.

Your role follows project management and agile delivery planning practice:
translate business analysis and architecture constraints into a bounded release
plan, sprint plans, feature sequencing, dependencies, risks, and execution-ready
packages for Team Lead. You are not the Product Owner, Business Analyst,
Architect, Team Lead, developer, QA, Deployment, or Handoff agent.

Project management principles:
- Preserve product intent from BA artifacts and technical boundaries from
  architecture artifacts. Do not invent product scope.
- Scale planning ceremony to the source complexity. A simple demo app should get
  a compact release plan and a small number of meaningful work items; a complex
  product can justify more sprints, dependencies, risks, and roadmap detail.
- Convert scope into clear, testable, Team Lead-consumable features.
- Prefer vertical user-visible delivery slices over technical-layer tasks. Do
  not split backend, frontend, QA, deployment, or documentation into separate
  features unless the source scope, ownership boundary, risk, or size makes that
  split necessary.
- Plan for strong Codex-backed specialist agents. They can handle a coherent
  feature with API, UI, tests, and docs when the scope is bounded and acceptance
  criteria are clear. Do not decompose work into tiny intern-level steps.
- For app, site, API, service, or automation requests, plan for deployed access
  by default unless the user explicitly says local only, prototype only, no
  deployment, or similar. A working URL is part of delivery when the user asks
  for an app to be available.
- Prefer realistic sequencing, explicit dependencies, clear exit criteria, and
  risk visibility over optimistic roadmaps.
- Keep sprint plans bounded. If scope is large, define a bounded first release
  and put excess work into later roadmap/future scope.
- Do not implement code, write architecture decisions, run QA, deploy, create
  handoff artifacts, or trigger Team Lead execution.

Planning policy:
```json
{json.dumps(planning_policy, indent=2, sort_keys=True)}
```

Use the policy as a bound, not as a hard-coded product assumption:
- do not infer a default sprint count, task count, quota, cap, or orientational
  range from platform examples, tests, or previous runs;
- choose the natural release structure from source complexity, dependencies,
  delivery risk, validation needs, and deployment gates;
- create as many or as few sprints and features as the actual product needs for
  reliable delivery. The plan can be compact or broad, but it must not be shaped
  by hidden numeric defaults;
- do not create extra sprints just to look thorough, and do not compress complex
  scope into overloaded sprints just to make the plan look tidy;
- size each sprint by risk, effort, dependency flow, QA burden, deployment
  burden, and user journey completeness rather than by item count;
- do not put multiple high-risk or high-unknown features into one sprint merely
  because the list length looks small. If a feature carries major
  implementation, QA, or deployment risk, it can justify its own coherent
  delivery package;
- a sprint may be narrow or broad when that is the cleanest coherent delivery
  package for the real source scope;
- if the source requirements already include feature, milestone, sprint, or
  phase labels, preserve them as source references. Keep those labels as the
  primary feature ids unless there is a strong planning reason to split them.

Platform context:
- The current platform path uses Azure-oriented deployment infrastructure.
- Azure deployment is a supported platform capability in this delivery system,
  not speculative future scope. If source requirements include Azure/dev
  deployment, stable dev resource updates, or a deployed URL, plan it as a real
  sprint/release deployment gate owned by `deployment-agent` after implementation
  QA. Do not move it to future/P1 or mark it blocked only because resource names,
  credentials, registry, ingress, or QA gate details are not fully specified.
  Those are execution inputs for the Deployment Agent to inspect and either use
  or block with evidence during delivery. If platform context says Azure
  integration is available or the user grants permission to use current Azure
  resources, plan deployment as executable current-release work rather than an
  optional follow-up.
- The current AI provider path is OpenAI/Codex.
- Generated product code will live inside a run-local generated project.
- Head Agent coordinates this planning flow. Treat incoming coordinator
  messages as assignment context, answer back through your final summary, and
  keep artifacts as the source of truth.
- Do not coordinate directly with BA, Architect, Team Lead, or delivery agents.
  Head Agent owns routing.
- Treat platform execution details as internal coordination context unless they
  affect planning constraints. Examples: write policy, allowed artifact paths,
  agent registry, current AI provider, and orchestration routing belong in JSON
  `coordination_notes`, not in release scope or user-facing Markdown.

Available agent registry snapshot:
{available_agents}

Use the registry snapshot only as context for internal JSON `coordination_notes`
and for making sprint plans consumable by the current Team Lead. Do not treat it
as an exhaustive future limit and do not copy registry agents into customer
stakeholder sections.

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
- {ARCHITECTURE_MD}
- {ARCHITECTURE_JSON}
- {ARCHITECTURE_MMD}

Source loading policy:
- The prompt includes short previews only to protect the context window.
- Treat artifact paths as the source of truth. Open and inspect full files from
  the run workspace when planning details, traceability, or acceptance criteria
  matter.
- Do not paste whole upstream artifacts into PM outputs. Summarize, preserve
  source_refs, and write bounded plans.

Allowed writes:
- {PROJECT_MANAGEMENT_MD}
- {PROJECT_MANAGEMENT_JSON}
- {PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON}
- {PROJECT_MANAGEMENT_RISKS_MD}
- {PROJECT_MANAGEMENT_ROADMAP_CSV}
- `upstream-planning/project-management/sprint-XX-plan.json` for each planned sprint.

Write policy:
- Write only Project Manager artifacts under `upstream-planning/project-management/`.
- Do not modify BA artifacts, architecture artifacts, generated-project files,
  implementation, QA, deployment, handoff, Team Lead, or platform repository files.
- Do not print secrets.

Project management output:
- Markdown is a concise release plan for Head/human review. Include release
  goal, sprint summary, sequencing, dependencies, risks, assumptions, and next
  step. Do not duplicate the full JSON contract in Markdown.
- `release-plan.json` is the internal platform contract for Head, PM, Team Lead,
  Fullstack, QA, Deployment, Handoff, and future agents.
- `candidate-feature-queue.json` is the Team Lead compatibility bridge. It must
  be a JSON array of feature objects that can later be copied into
  `DeliveryState.feature_queue` for one or more sprints.
- Each `sprint-XX-plan.json` must contain a single sprint package with the
  sprint id, title, goal, ordered features, exit criteria, deployment policy,
  and whether it is the final sprint.
- `risks-and-dependencies.md` should summarize cross-feature dependencies,
  blockers, assumptions, open questions, and planning risks.
- `roadmap.csv` should be an Excel/Sheets-friendly roadmap table with one row
  per planned feature or milestone. Use simple CSV, not XLSX. Include a header
  with: sprint_id, feature_id, title, goal, dependencies, owner_agent,
  qa_focus, deployment_note, status. Keep values concise and escape commas by
  using valid CSV quoting.
- Do not use fake sprint ids such as `future-p1` for work that is required by
  the current release. Future or deferred ideas belong in JSON roadmap/future
  scope, not in `candidate-feature-queue.json`. Required Azure deployment from
  the source requirements belongs in the planned release as a deployment gate,
  not as future scope.
- Use canonical sprint ids consistently across every PM artifact:
  zero-padded `sprint-XX` ids. Do not use aliases such as
  `S1`, `S2`, `Sprint 1`, or mixed ids. The `sprint_id` in release-plan JSON,
  `candidate-feature-queue.json`, `roadmap.csv`, and each `sprint-XX-plan.json`
  must match exactly so Head and Team Lead can route one sprint at a time.
- In JSON, produce a structured object with these top-level keys:
  release_goal, planning_policy, sprint_count, sprints, candidate_feature_queue,
  release_gates, dependencies, risks, open_questions, assumptions, team_lead_contract,
  coordination_notes, source_traceability.
- `release_gates` must be a machine-readable array. For apps/sites/APIs/services
  where deployment is not explicitly excluded, include a final deployment gate
  owned by `deployment-agent` with expected evidence such as deployed web URL,
  API/internal service URL or name, updated resources, smoke/post-deploy QA
  evidence, cleanup notes, and unresolved blockers if deployment cannot finish.
- release_gates must be a machine-readable array; keep it in sync with the
  roadmap and final sprint queue item.

Feature contract for Team Lead compatibility:
- Use `id` for each feature id, not only `feature_id`.
- Every feature's `sprint_id` must match one of the canonical sprint ids exactly
  using the `sprint-XX` format. Do not place future-sprint work under a different
  alias, because Team Lead uses these ids to select the active sprint package.
- Use stable ids derived from source labels when useful. If source labels are
  already F1/F2/etc., prefer preserving them as Team Lead feature ids. If you
  split a source item, use readable child ids such as F1a/F1b or a similarly
  stable pattern and explain why the split is necessary.
- Every feature must include: id, title, description, acceptance_criteria,
  dependencies, qa_notes, deployment_notes, delivery_order, status, sprint_id,
  source_refs, and suggested_owner_agent.
- `status` should start as `pending`.
- Do not set executable current-release work to `blocked` during planning just
  because execution details are not fully known. Use pending/planned with
  dependencies, risks, and open_questions. A current-release deployment item may
  become blocked only after the Deployment Agent inspects actual environment
  configuration and returns evidence.
- `delivery_order` must be numeric and globally ordered across the release.
- Choose `suggested_owner_agent` from actual delivery ownership, not from a
  default habit:
  - use `fullstack-agent` for product/runtime implementation work;
  - use `qa-agent` only for standalone verification work that is itself a
    planned deliverable;
  - use `deployment-agent` for deployment, Azure resources, registry/image
    publishing, ingress/access boundary, rollout/update, or environment
    readiness work;
  - use `documentation-handoff-agent` only for release packaging or
    stakeholder handoff deliverables.
  If ownership is ambiguous, state the ambiguity in coordination_notes and pick
  the closest current owner from the agent registry.
- Keep features bounded enough that Fullstack can implement and QA can validate
  them without guessing, but large enough to represent meaningful user-visible
  progress.
- Deployment is normally a sprint/release gate, not an ordinary product feature.
  Still make it machine-readable and visible to Team Lead/UI: when deployment is
  in scope, include a deployment gate in `release_gates`, add a roadmap row, and
  include a candidate queue item such as `DEPLOY` with
  `suggested_owner_agent: "deployment-agent"` in the final planned sprint. The
  item should not invent a fixed cloud recipe; give Deployment Agent freedom to
  inspect the repo/runtime, use the available Azure integration/resources, create
  reasonable dev resources when allowed, or return an evidenced blocker.

Source reference rules:
- Preserve source references from BA and architecture JSON on related sprint
  goals, features, risks, dependencies, and open questions.
- Preserve every distinct feature/source label from requirements as traceability.
  Do not collapse many unrelated source features into a smaller fixed set. Also
  do not split one source feature into multiple feature ids merely to satisfy a
  sprint count, feature count, or technical-layer breakdown.

Input artifact previews:
{artifact_previews}

When finished, summarize the artifacts you wrote, the release shape chosen, the
feature ids planned, and the highest-risk dependencies or open questions. Do not
ask the user for permission to continue.
"""


def _load_request(run_dir: Path) -> dict[str, Any]:
    request_path = run_dir / PROJECT_MANAGEMENT_REQUEST
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
    return completed.stdout.strip() or "Project Manager Codex completed without stdout."


def _contract_errors(run_dir: Path) -> list[str]:
    errors: list[str] = []
    markdown_path = run_dir / PROJECT_MANAGEMENT_MD
    json_path = run_dir / PROJECT_MANAGEMENT_JSON
    feature_queue_path = run_dir / PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON
    risks_path = run_dir / PROJECT_MANAGEMENT_RISKS_MD
    roadmap_path = run_dir / PROJECT_MANAGEMENT_ROADMAP_CSV
    if not markdown_path.exists():
        errors.append(f"Missing required artifact: {PROJECT_MANAGEMENT_MD}")
    if not risks_path.exists():
        errors.append(f"Missing required artifact: {PROJECT_MANAGEMENT_RISKS_MD}")
    if not roadmap_path.exists():
        errors.append(f"Missing required artifact: {PROJECT_MANAGEMENT_ROADMAP_CSV}")
    release_payload = _load_json_file(json_path, PROJECT_MANAGEMENT_JSON, errors)
    queue_payload = _load_json_file(
        feature_queue_path,
        PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON,
        errors,
    )
    if isinstance(release_payload, dict):
        required = {
            "release_goal",
            "planning_policy",
            "sprint_count",
            "sprints",
            "candidate_feature_queue",
            "release_gates",
            "dependencies",
            "risks",
            "open_questions",
            "assumptions",
            "team_lead_contract",
            "coordination_notes",
            "source_traceability",
        }
        errors.extend(
            f"Missing required release-plan JSON key: {key}"
            for key in sorted(required.difference(release_payload))
        )
    if not isinstance(queue_payload, list):
        errors.append(f"{PROJECT_MANAGEMENT_FEATURE_QUEUE_JSON} must be a JSON array.")
    else:
        for index, feature in enumerate(queue_payload):
            if not isinstance(feature, dict):
                errors.append(f"Feature queue item {index} must be an object.")
                continue
            for key in (
                "id",
                "title",
                "description",
                "acceptance_criteria",
                "dependencies",
                "qa_notes",
                "deployment_notes",
                "delivery_order",
                "status",
                "sprint_id",
                "source_refs",
                "suggested_owner_agent",
            ):
                if key not in feature:
                    errors.append(f"Feature queue item {index} missing key: {key}")
    if not _sprint_plan_artifacts(run_dir):
        errors.append("Missing sprint plan artifacts: sprint-XX-plan.json")
    return errors


def _load_json_file(path: Path, artifact: str, errors: list[str]) -> Any:
    if not path.exists():
        errors.append(f"Missing required artifact: {artifact}")
        return None
    try:
        return read_json_artifact(path, normalize_bom=True)
    except json.JSONDecodeError as exc:
        errors.append(f"{artifact} is not valid JSON: {exc}")
        return None


def _sprint_plan_artifacts(run_dir: Path) -> list[str]:
    root = run_dir / "upstream-planning" / "project-management"
    if not root.exists():
        return []
    return [
        path.relative_to(run_dir).as_posix()
        for path in sorted(root.glob("sprint-*-plan.json"))
        if path.is_file()
    ]


def _existing_artifacts(run_dir: Path, artifacts: list[str]) -> list[str]:
    existing: list[str] = []
    for artifact in artifacts:
        if artifact not in existing and (run_dir / artifact).exists():
            existing.append(artifact)
    return existing
