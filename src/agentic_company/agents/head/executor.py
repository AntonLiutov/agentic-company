"""LangChain runtime for the Head Agent."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentic_company.agents.head.contracts import HEAD_TOOL_CONTRACT_REGISTRY, HEAD_TOOLS
from agentic_company.agents.head.tools import HeadExecutorResult, HeadToolbox, HeadWorkers
from agentic_company.agents.registry import agent_by_id
from agentic_company.platform.agent.agent_runtime import (
    AGENT_REASONING_EFFORT_ENV,
    COORDINATOR_AGENT_REASONING_EFFORT_ENV,
    DEFAULT_COORDINATOR_AGENT_REASONING_EFFORT,
    LangChainAgentRequest,
    LangChainAgentRuntimeError,
    LangChainCreateAgentRuntime,
    MissingAgentRuntimeConfig,
    coordinator_quality_review_policy,
    coordinator_repair_policy,
)
from agentic_company.platform.contracts.tool_contracts import render_tool_docstring
from agentic_company.platform.db.state import DeliveryState


class LangChainHeadExecutor:
    """LangChain-backed Head Agent executor."""

    def __init__(self, runtime: LangChainCreateAgentRuntime | None = None) -> None:
        self.runtime = runtime or LangChainCreateAgentRuntime()

    def run(
        self,
        *,
        delivery_state: DeliveryState,
        workers: HeadWorkers,
        max_steps: int,
    ) -> HeadExecutorResult:
        toolbox = HeadToolbox(
            delivery_state=delivery_state,
            workers=workers,
            max_steps=max_steps,
        )

        def finish_if_stopped() -> bool:
            if not toolbox.stop_requested():
                return False
            toolbox.mark_stopped()
            return True

        if finish_if_stopped():
            return toolbox.result()

        try:
            self.runtime.invoke(
                LangChainAgentRequest(
                    agent_id="head-agent",
                    system_prompt=HEAD_SYSTEM_PROMPT,
                    user_prompt=build_head_executor_prompt(delivery_state=toolbox.delivery_state),
                    tools=langchain_tools(toolbox),
                    delivery_state=delivery_state,
                    max_steps=max_steps,
                    stage="head",
                    default_reasoning_effort=DEFAULT_COORDINATOR_AGENT_REASONING_EFFORT,
                    reasoning_effort_env_keys=(
                        "HEAD_AGENT_REASONING_EFFORT",
                        COORDINATOR_AGENT_REASONING_EFFORT_ENV,
                        AGENT_REASONING_EFFORT_ENV,
                    ),
                )
            )
        except MissingAgentRuntimeConfig:
            if finish_if_stopped():
                return toolbox.result()
            toolbox.block_planning(
                reason="The selected planning provider is not connected for Head Agent decisions.",
                message="Connect the selected planning provider in Settings.",
            )
            return toolbox.result()
        except LangChainAgentRuntimeError as exc:
            if finish_if_stopped():
                return toolbox.result()
            toolbox.block_planning(
                reason=f"LangChain Head Agent dependencies are missing: {exc}",
                message="Install langchain and langchain-openai.",
            )
            return toolbox.result()
        except Exception as exc:  # pragma: no cover - exercised through integration runs
            if finish_if_stopped():
                return toolbox.result()
            toolbox.block_planning(
                reason=f"Head Agent executor failed: {exc}",
                message="Executor failed before completing upstream planning.",
            )
            return toolbox.result()

        if finish_if_stopped():
            return toolbox.result()
        if not toolbox.tool_calls_made():
            toolbox.block_planning(
                reason="Head Agent executor completed without calling any tool.",
                message="The Head Agent must use tools to coordinate planning.",
            )
        elif not toolbox.reached_terminal_state():
            toolbox.block_incomplete_execution()
        return toolbox.result()


def build_head_executor_prompt(*, delivery_state: DeliveryState) -> str:
    context = {
        "mission": (
            "Coordinate upstream planning by calling tools. First ask the Business Analyst "
            "to analyze the raw requirements. Then ask the Architect to create architecture "
            "from the BA artifacts and response. Then ask Project Manager to create a bounded "
            "release/sprint plan from BA and architecture artifacts. Then ask Team Lead to "
            "execute the Project Manager planned work item contract through the delivery team."
        ),
        "delivery_state": _compact_delivery_state(delivery_state),
        "available_tools": list(HEAD_TOOLS),
        "communication_context": _communication_context(),
        "coordinator_quality_review_policy": coordinator_quality_review_policy(
            coordinator_name="Head Agent",
            downstream_tools=[
                "run_business_analyst",
                "run_architect",
                "run_project_manager",
                "run_team_lead",
            ],
            repair_limit=int(delivery_state.get("max_repair_attempts", 5)),
        ),
        "coordinator_repair_policy": coordinator_repair_policy(
            coordinator_name="Head Agent",
            downstream_tools=[
                "run_business_analyst",
                "run_architect",
                "run_project_manager",
                "run_team_lead",
            ],
            repair_limit=int(delivery_state.get("max_repair_attempts", 5)),
        ),
        "required_behavior": [
            "Call tools directly; do not merely describe a plan.",
            "Call run_business_analyst first unless BA artifacts already exist and are current.",
            (
                "Scale every specialist assignment to the source request complexity. For a "
                "simple demo app, ask for concise, execution-ready outputs in the specialist's "
                "standard allowed artifacts; do not inflate the request into enterprise "
                "deliverable sets, exhaustive analysis packs, or many separate extra files."
            ),
            (
                "When composing message fields, do not prescribe long custom deliverable "
                "lists. Point to the source artifacts, state the business outcome, ask the "
                "specialist to follow its own contract, and ask for artifact refs."
            ),
            (
                "Treat deployable access as the default expectation for apps, sites, APIs, "
                "services, and automations unless the user explicitly says local-only, no "
                "deployment, or similar. Keep deployment in the release path instead of "
                "turning it into optional future scope."
            ),
            (
                "Apply coordinator_quality_review_policy after every meaningful Business "
                "Analyst, Architect, Project Manager, and Team Lead result. Do not move to "
                "the next phase just because the tool returned completed; review the artifacts "
                "and result first."
            ),
            "Call run_architect only after Business Analyst completed successfully.",
            (
                "After Architect completes successfully, call run_project_manager with BA and "
                "architecture artifact refs and ask for the smallest reasonable Team "
                "Lead-consumable release plan. Tell PM to prefer vertical source-labeled "
                "features and not split work merely to fill sprint or feature counts."
            ),
            (
                "Apply coordinator_repair_policy to Business Analyst, Architect, Project "
                "Manager, and Team Lead tool responses. For example, if any of those agents "
                "returns failed/blocked/contract-error status, inspect the response and "
                "artifact refs, call codex_review after meaningful downstream work, then "
                "rerun that same owning tool with concrete repair advice when the issue is "
                "repairable."
            ),
            (
                "After Project Manager completes successfully, call run_team_lead with PM "
                "artifact refs and a clear sprint-delivery assignment. Use the exact "
                "canonical sprint_id from PM artifacts as the run_team_lead sprint_id; do "
                "not invent alternate ids such as Sprint 01, S1, or a default sprint id when "
                "PM provided a concrete id."
            ),
            (
                "When delivery starts after PM planning, call inspect_delivery_status before "
                "the first run_team_lead. The tool returns delivery status inspection readback, "
                "and you must use it only as delivery status/evidence readback. Do not let "
                "the inspection choose the next tool."
            ),
            (
                "After every run_team_lead result, call inspect_delivery_status before "
                "deciding whether to continue the same sprint, start the next sprint, or "
                "complete delivery. The status_legend explains what each status means."
            ),
            (
                "`team_lead_sprint_handoff_ready` means the addressed sprint handoff is "
                "ready; it does not by itself mean the whole project delivery is complete. "
                "Before calling complete_delivery, inspect PM's planned sprints, "
                "planned_work_items, work_items, sprint-XX-plan artifacts, and "
                "release/deployment gates. If any later sprint, pending feature, or final "
                "deployment gate remains, call run_team_lead again with the next sprint_id "
                "and a message pointing to that sprint plan. Only call "
                "complete_delivery after every PM-planned sprint and the final deployment/"
                "handoff gate are complete or explicitly out of scope. Never translate one "
                "sprint handoff into project completion; use release/deployment gates to "
                "find the next sprint_id generically."
            ),
            (
                "After Team Lead completes successfully or returns "
                "team_lead_sprint_handoff_ready, review only the artifact refs actually "
                "returned by Team Lead/Handoff and DB artifact metadata. "
                "Compare them only against the user request, active PM sprint plan, active "
                "board item, and Team Lead/Handoff contract. Do not invent a separate sprint "
                "report path, expected folder, alternate id path, or release-governance gate. "
                "If Codex Review finds the returned/registered handoff artifacts exist and "
                "substantially satisfy the PM/board DoD, accept those canonical refs even if "
                "they live under a different folder than you expected. If another sprint "
                "remains, call run_team_lead with that sprint_id. If no sprint remains, "
                "ensure the final project handoff exists through Team Lead and then call "
                "complete_delivery. Do not rerun the same completed sprint just to republish "
                "already readable artifacts under a different path. Never require a Team "
                "Lead wrapper folder, alternate id report, deliverables pointer, or copied file "
                "when Handoff-owned canonical refs are already returned or registered."
            ),
            (
                "Head Agent is a coordinator, not a release auditor. Never require repo URL, "
                "branch, commit SHA, PR URL, CI workflow, staging URL, deployment run link, "
                "or similar traceability/governance metadata as a sprint blocker unless the "
                "user request or PM sprint plan explicitly requires that exact item. If Codex "
                "Review suggests such items without a PM/user requirement, treat them as "
                "optional notes and continue routing."
            ),
            (
                "Head has no normal block authority from Codex Review. Codex Review is "
                "advisory-only inspection: use it to understand issues, suggest repair help, "
                "or route a PM-required repair, but never to stop delivery on newly invented "
                "review criteria."
            ),
            (
                "When asking Codex Review about Team Lead output, phrase the question as: "
                "review only against the active PM sprint plan and returned artifact refs; "
                "do not add new acceptance gates; separate optional suggestions from blockers."
            ),
            (
                "If Team Lead says work is complete but does not return artifact refs, first "
                "inspect the downstream_response and DB artifact metadata for "
                "handoff artifacts. If refs exist there, use them. If they do not exist, ask "
                "Team Lead to report or produce missing evidence refs through its normal "
                "tool response; do not prescribe a new folder or filename."
            ),
            (
                "Never call complete_delivery unless the latest inspect_delivery_status "
                "readback says can_complete_delivery=true."
            ),
            "Do not call Fullstack, QA, Deployment, or Handoff directly; Team Lead owns them.",
            (
                "Use the message field as real agent-to-agent communication. Tell each "
                "specialist what you need, which artifacts matter, and what the next "
                "coordinator will use their answer for."
            ),
            (
                "Do not prescribe exact output paths unless the specialist contract or tool "
                "response lists them. Ask specialists to write their allowed artifacts and "
                "return artifact refs."
            ),
            (
                "Call block_planning only when a downstream planning/delivery agent cannot "
                "complete, returned a real non-repairable blocker, or exhausted bounded "
                "Repair. Do not stop delivery from advisory review advice alone."
            ),
        ],
    }
    return json.dumps(context, indent=2, sort_keys=True)


HEAD_SYSTEM_PROMPT = """You are the Head Agent for agentic-company.

You coordinate the upstream planning team by calling specialist tools. You do
not write business-analysis artifacts yourself, write architecture artifacts
yourself, write project-management artifacts yourself, implement code, run QA,
deploy, or create handoff directly.

Current bounded flow:
- ask Business Analyst to analyze the raw product requirements;
- read the Business Analyst tool response and artifacts;
- ask Architect to produce product/system architecture from BA output;
- read the Architect tool response and artifacts;
- ask Project Manager to produce the smallest reasonable bounded release plan,
  sprint plans, and Team Lead-compatible planned work item contract from BA and
  architecture output;
- read the Project Manager tool response and artifacts;
- call `inspect_delivery_status` once delivery starts and use its JSON readback
  only to confirm delivery status, sprint statuses, gates, blockers, and
  completion booleans;
- if any planning/delivery specialist returns failed, blocked, or contract
  errors after meaningful downstream work, inspect the response and artifact
  refs, use Codex Review, then ask the owning specialist for bounded repair
  passes when repairable;
- ask Team Lead to execute sprint delivery from DB-materialized PM work items;
- read the Team Lead tool response and artifacts;
- after each Team Lead result, call `inspect_delivery_status`; it reads the
  independent delivery status inspection readback. Treat the readback as
  status-only evidence; choose the next sprint/tool from PM artifacts and the
  Head workflow;
- after each successful sprint, continue to the next pending sprint from the DB
  work item table; do not rerun a sprint that is already handoff-ready;
- after the final sprint, let Team Lead produce the project/final handoff, then
  complete the company run only when the latest delivery status inspection says
  `can_complete_delivery=true`;
- complete the company run.

Specialist tool calls are agent-to-agent communication. Put meaningful
instructions in each tool's `message` field. The specialist will receive that
message as upstream context and will answer back through the tool response.

Calibrate every assignment to the actual request. A small internal demo app
should receive small, clear, execution-ready planning instructions. A complex
regulated or multi-system product can justify deeper artifact detail. Do not
ask every specialist for the same enterprise-sized checklist. Do not prescribe
long custom deliverable lists such as many separate BA, architecture, schedule,
or traceability files unless the source request genuinely needs that depth and
the specialist contract allows those files.

For Business Analyst, Architect, and Project Manager, normally ask them to use
their standard allowed artifacts and return artifact refs. Your message should
state the source artifacts, the outcome you need, important constraints, and who
will consume the result. Let the specialist's own prompt decide the exact
internal structure within its allowed artifacts.

For apps, sites, APIs, services, and automations, deployable access is the
default delivery expectation unless the user explicitly says local-only, no
deployment, prototype-only without deployment, or similar. Keep deployment in
the planned delivery path and assign it to Team Lead/Deployment through the PM
planned work item contract instead of treating it as optional future scope.

Codex Review is the internal reviewer for coordinator acceptance. Use it after
meaningful downstream results to verify quality, artifact consistency, contract
completeness, and readiness before moving to the next stage. If review finds
material gaps, send the review feedback back to the owning specialist through
that specialist's normal tool. Do not use Codex Review as a replacement for the
specialist that owns the artifacts.

Codex Review is not allowed to expand sprint acceptance scope. It may only judge
against the user request, the active Project Manager sprint plan/board item, the
specialist contract, and returned artifact refs. Repo URL, branch, commit SHA,
PR URL, CI workflow, staging URL, deployment run link, extra reports, and other
release-governance metadata are optional unless the user request or PM sprint
plan explicitly requires them. If review recommends those items without an
explicit PM/user requirement, treat them as optional notes, not blockers.
Head has no normal block authority from Codex Review. Review feedback is
advisory: use it to clarify, repair a PM-required gap, or route the next planned
tool call, but do not stop the run solely because Review suggested extra
governance.

For Team Lead completion, use the artifact refs returned by Team Lead/Handoff as
the contract. Do not invent a separate sprint report path or require a report
under Project Manager folders unless Team Lead actually returned that artifact.
Do not ask Codex Review to check a suggested Team Lead folder unless that folder
was returned by Team Lead as an artifact ref. Codex Review should inspect the
returned refs and DB artifact metadata first. If it finds readable
handoff artifacts that satisfy the delivery evidence, accept them instead of
asking Team Lead to duplicate or rename files.
Handoff-owned refs under `handoff/sprints/...` and `handoff/project/final/...`
are canonical delivery evidence. Do not require Team Lead to copy them into
`upstream-planning`, create wrapper deliverables files, or publish duplicate reports
unless a downstream tool explicitly returned that as its own contract.
If Team Lead's wording says a sprint or project is complete but the direct
response omits artifact refs, search the tool response and DB artifact metadata
for Handoff-owned refs before asking for repair. A repair
request should ask for missing evidence refs or genuinely missing artifacts, not
for a different folder layout.
`team_lead_sprint_handoff_ready` is sprint-level completion evidence unless the
PM plan shows no later sprint, no pending feature, and no final deployment gate.
Never translate one sprint handoff into project completion. Use PM's planned
sprints, planned_work_items, work_items, sprint-XX-plan artifacts, and
release/deployment gates to find the next sprint_id generically.
If Team Lead returns `team_lead_sprint_handoff_ready` with sprint handoff
artifacts and there are no real blockers, inspect DB work items
for pending later sprints through `inspect_delivery_status`. If another sprint
remains, call `run_team_lead` with that sprint id. If no planned sprint remains,
expect Team Lead to produce a final project handoff and then call
`complete_delivery` only after status-inspection confirmation. Do not rerun the
same completed sprint.

The communication shape is Head Agent <-> Business Analyst,
Head Agent <-> Architect, Head Agent <-> Project Manager, and
Head Agent <-> Team Lead. Do not instruct downstream agents to coordinate
directly with each other unless their tool contract explicitly allows it.
"""


def langchain_tools(toolbox: HeadToolbox) -> list[Callable[..., str]]:
    def run_business_analyst(
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate raw requirements analysis to the Business Analyst Agent."""
        return toolbox.run_business_analyst(reason, message, artifact_refs, external_reference)

    def run_architect(
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate solution architecture to the Architect Agent after BA completes."""
        return toolbox.run_architect(reason, message, artifact_refs, external_reference)

    def run_project_manager(
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate release and sprint planning to the Project Manager Agent after Architect."""
        return toolbox.run_project_manager(reason, message, artifact_refs, external_reference)

    def run_team_lead(
        sprint_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate one PM-planned sprint to Team Lead with an explicit sprint_id."""
        return toolbox.run_team_lead(sprint_id, reason, message, artifact_refs, external_reference)

    def codex_review(
        target_agent: str = "",
        purpose: str = "",
        question: str = "",
        artifact_refs: str = "",
        intent: str = "review_feedback",
        correlation_id: str = "upstream-planning",
        reason: str = "",
        message: str = "",
    ) -> str:
        """Run read-only advisory Codex analysis and send feedback when a known target is set."""
        return toolbox.codex_review(
            target_agent=target_agent,
            purpose=purpose,
            question=question,
            artifact_refs=artifact_refs,
            intent=intent,
            correlation_id=correlation_id,
            reason=reason,
            message=message,
        )

    def inspect_delivery_status(
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
    ) -> str:
        """Run delivery status inspection and read back structured evidence."""
        return toolbox.inspect_delivery_status(reason, message, artifact_refs)

    def complete_delivery(
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
    ) -> str:
        """Mark the company delivery run complete after inspection confirms readiness."""
        return toolbox.complete_delivery(reason, message, artifact_refs)

    def block_planning(
        reason: str,
        correlation_id: str = "upstream-planning",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Block planning when bounded Repair cannot continue."""
        return toolbox.block_planning(
            reason=reason,
            correlation_id=correlation_id,
            message=message,
            artifact_refs=artifact_refs,
            external_reference=external_reference,
        )

    tools = [
        run_business_analyst,
        run_architect,
        run_project_manager,
        run_team_lead,
        codex_review,
        inspect_delivery_status,
        complete_delivery,
        block_planning,
    ]
    for tool in tools:
        contract = HEAD_TOOL_CONTRACT_REGISTRY.maybe_get(tool.__name__)
        if contract:
            tool.__doc__ = render_tool_docstring(contract)
    return tools


def _compact_delivery_state(state: DeliveryState) -> dict[str, Any]:
    keys = [
        "run_id",
        "stage",
        "status",
        "team_lead_sprint_id",
        "completed_nodes",
        "blockers",
    ]
    compact = {key: state.get(key) for key in keys if key in state}
    compact["source_requirements_ref"] = "00-requirements.md"
    return compact


def _communication_context() -> dict[str, Any]:
    specialists: list[dict[str, str]] = []
    for agent_id, tool, intent in [
        ("business-analyst-agent", "run_business_analyst", "request_business_analysis"),
        ("architect-agent", "run_architect", "request_architecture"),
        ("project-manager-agent", "run_project_manager", "request_project_management"),
        ("team-lead-agent", "run_team_lead", "request_sprint_delivery"),
    ]:
        descriptor = agent_by_id(agent_id)
        specialists.append(
            {
                "agent_id": descriptor.agent_id,
                "name": descriptor.name,
                "stage": descriptor.stage,
                "tool": tool,
                "request_intent": intent,
                "relationship": f"Head Agent <-> {descriptor.name}",
            }
        )
    return {
        "active_specialists": specialists,
        "paused_roles": [
            "fullstack-agent",
            "qa-agent",
            "deployment-agent",
            "documentation-handoff-agent",
        ],
        "message_rule": (
            "Use tool message fields for assignments. Specialist responses return through "
            "agent_response messages and tool responses."
        ),
    }
