"""LangChain runtime for the Team Lead agent."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from agentic_company.agents.team_lead.contracts import (
    TEAM_LEAD_TOOL_CONTRACT_REGISTRY,
    TEAM_LEAD_TOOLS,
)
from agentic_company.agents.team_lead.tools import (
    TeamLeadExecutorResult,
    TeamLeadToolbox,
    TeamLeadWorkers,
)
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
from agentic_company.platform.db.state import DeliveryState
from agentic_company.platform.contracts.tool_contracts import render_tool_docstring

TEAM_LEAD_COORDINATION_WORK_ITEM_ID = "PLAN-04"


class LangChainTeamLeadExecutor:
    """LangChain-backed Team Lead AgentExecutor."""

    def __init__(self, runtime: LangChainCreateAgentRuntime | None = None) -> None:
        self.runtime = runtime or LangChainCreateAgentRuntime()

    def run(
        self,
        *,
        delivery_state: DeliveryState,
        sprint: dict[str, Any],
        workers: TeamLeadWorkers,
        max_steps: int,
    ) -> TeamLeadExecutorResult:
        toolbox = TeamLeadToolbox(
            delivery_state=delivery_state,
            sprint=sprint,
            workers=workers,
            max_steps=max_steps,
        )

        def _invoke(force_tool_call: bool) -> None:
            self.runtime.invoke(
                LangChainAgentRequest(
                    agent_id="team-lead-agent",
                    system_prompt=TEAM_LEAD_SYSTEM_PROMPT,
                    user_prompt=build_team_lead_executor_prompt(
                        delivery_state=toolbox.delivery_state,
                        sprint=sprint,
                        force_tool_call=force_tool_call,
                    ),
                    tools=langchain_tools(toolbox),
                    delivery_state=delivery_state,
                    max_steps=max_steps,
                    stage="team_lead",
                    default_reasoning_effort=DEFAULT_COORDINATOR_AGENT_REASONING_EFFORT,
                    reasoning_effort_env_keys=(
                        "TEAM_LEAD_AGENT_REASONING_EFFORT",
                        COORDINATOR_AGENT_REASONING_EFFORT_ENV,
                        AGENT_REASONING_EFFORT_ENV,
                    ),
                )
            )

        try:
            _invoke(force_tool_call=False)
            # Defense-in-depth: a single empty model turn must not sink a sprint that
            # still has pending DB work. Re-invoke once with a forcing directive before
            # the no-tool-call path escalates to a hard sprint block.
            if (
                not toolbox.tool_calls_made()
                and "team_lead_sprint_handoff_ready"
                not in str(toolbox.delivery_state.get("status") or "")
                and _sprint_has_pending_work(sprint)
            ):
                _invoke(force_tool_call=True)
        except MissingAgentRuntimeConfig:
            toolbox.block_sprint(
                reason="OPENAI_API_KEY is required for Team Lead AgentExecutor decisions.",
                work_item_id=TEAM_LEAD_COORDINATION_WORK_ITEM_ID,
                message="Set OPENAI_API_KEY in Settings or the run-level agent runtime env.",
            )
            return toolbox.result()
        except LangChainAgentRuntimeError as exc:
            toolbox.block_sprint(
                reason=f"LangChain Team Lead AgentExecutor dependencies are missing: {exc}",
                work_item_id=TEAM_LEAD_COORDINATION_WORK_ITEM_ID,
                message="Install langchain and langchain-openai.",
            )
        except Exception as exc:  # pragma: no cover - exercised through integration runs
            toolbox.block_sprint(
                reason=f"Team Lead AgentExecutor failed: {exc}",
                work_item_id=TEAM_LEAD_COORDINATION_WORK_ITEM_ID,
                message="Executor failed before completing the sprint.",
            )

        if not toolbox.tool_calls_made() and "team_lead_sprint_handoff_ready" not in str(
            toolbox.delivery_state.get("status") or ""
        ):
            toolbox.block_sprint(
                reason="Team Lead AgentExecutor completed without calling any tool.",
                work_item_id=TEAM_LEAD_COORDINATION_WORK_ITEM_ID,
                message="The Team Lead must use tools to coordinate the sprint.",
            )
        elif not toolbox.reached_terminal_state():
            toolbox.block_incomplete_execution()
        return toolbox.result()


def _sprint_has_pending_work(sprint: dict[str, Any]) -> bool:
    """True when the seeded DB board still has todo/blocked/in-progress items."""
    completion = sprint.get("completion_state") or {}
    if completion:
        return (
            int(completion.get("pending_items", 0)) > 0
            or int(completion.get("blocked_items", 0)) > 0
        )
    return any(
        str(item.get("status")) in {"todo", "blocked", "in_progress", "review"}
        for item in sprint.get("work_items", []) or []
    )


def build_team_lead_executor_prompt(
    *,
    delivery_state: DeliveryState,
    sprint: dict[str, Any],
    force_tool_call: bool = False,
) -> str:
    context = {
        "mission": (
            "Execute the active sprint package by calling tools. Work item-by-item from "
            "the Project Manager sprint plan and roadmap. Each work item is handled by "
            "its suggested owner, then validated by QA when owner work is produced. "
            "After all active sprint work passes QA, follow the sprint deployment policy: "
            "deploy and run post-deploy QA only when the policy requires deployment; "
            "otherwise request handoff from local/QA evidence and close the sprint."
        ),
        "sprint": sprint,
        "delivery_state": _compact_delivery_state(delivery_state),
        "upstream_planning_context": _upstream_planning_context(delivery_state),
        "available_tools": list(TEAM_LEAD_TOOLS),
        "coordinator_quality_review_policy": coordinator_quality_review_policy(
            coordinator_name="Team Lead",
            downstream_tools=[
                "run_fullstack",
                "run_qa",
                "run_deployment",
                "run_post_deploy_qa",
                "run_handoff",
            ],
            repair_limit=int(delivery_state.get("max_repair_attempts", 5)),
        ),
        "coordinator_repair_policy": coordinator_repair_policy(
            coordinator_name="Team Lead",
            downstream_tools=[
                "run_fullstack",
                "run_qa",
                "run_deployment",
                "run_post_deploy_qa",
                "run_handoff",
            ],
            repair_limit=int(delivery_state.get("max_repair_attempts", 5)),
        ),
        "required_behavior": [
            "Call tools directly; do not merely describe a plan.",
            (
                "At sprint start, orient yourself from upstream_planning_context and PM/board "
                "artifacts before delegating work. First call inspect_sprint_status; it reads "
                "the sprint status inspection readback, and you should use it only as "
                "status/evidence readback. Do not let the inspection choose the next worker. "
                "If the artifact set is large, unclear, or potentially conflicting, call "
                "codex_review for read-only orientation across BA, architecture, and PM "
                "artifacts."
            ),
            (
                "Treat DB work_items as the sprint board. Pick the next sprint item from "
                "the canonical DB rows and call each specialist with explicit work_item_id."
            ),
            (
                "Use PM suggested_owner_agent as the default owner, but apply real delivery "
                "ownership if the suggested owner is obviously wrong. Product/runtime "
                "implementation belongs to Fullstack; verification belongs to QA; deployment, "
                "Azure resources, image/registry, ingress/access, rollout, and environment "
                "readiness belong to Deployment; stakeholder release packaging belongs to "
                "Handoff. Explain the correction in the tool message instead of silently "
                "routing to the wrong specialist."
            ),
            (
                "For a product/runtime implementation item with no QA failure or fix request "
                "yet, ask Fullstack to implement it. Use repair wording only after QA reports "
                "a failure or returns fix request artifacts."
            ),
            (
                "After owner work returns implementation artifacts, ask QA to validate that "
                "same work item. QA owns testing decisions; Team Lead owns the assignment and "
                "status movement."
            ),
            (
                "After every run_fullstack, run_qa, run_deployment, run_post_deploy_qa, "
                "run_handoff, and codex_review call, call inspect_sprint_status before "
                "choosing the next worker or terminal action. Use the inspection readback "
                "only to confirm task statuses, gates, blockers, and completion booleans; "
                "routing remains your responsibility."
            ),
            (
                "When every current-sprint work item is done, decide the release gate from PM "
                "roadmap/sprint policy and actual delivery state. If the sprint includes a "
                "deployment/update gate, call deployment, then post-deploy QA, then handoff. "
                "If it is local-only or deployment is explicitly not part of this sprint, call "
                "handoff from local QA evidence."
            ),
            (
                "Treat Azure/dev deployment as a supported platform path when PM/source "
                "requirements include it. Do not skip or defer deployment merely because "
                "resource details may need inspection. The Deployment Agent owns that "
                "inspection and may use configured Azure integration or return an evidenced "
                "blocker."
            ),
            (
                "Never select or run work from a different sprint unless Head changed the "
                "active sprint."
            ),
            (
                "When calling a specialist tool, use the message field to send the downstream "
                "agent the concrete request, context, questions, or concerns it should consider. "
                "The tool will attach the canonical work item packet automatically. Do not "
                "invent stricter acceptance criteria, status codes, feature scope, deployment "
                "gates, or QA gates in free text unless they are present in the PM work item or "
                "cited artifacts. The tool response includes the downstream agent response "
                "message and artifact refs."
            ),
            (
                "After meaningful downstream results, do a lightweight coordinator sanity "
                "review before moving on: confirm the expected artifacts/summary exist, the "
                "response matches the requested sprint/work item, and there is no obvious "
                "blocked/failed/mismatched status. Do not redo specialist work or QA in this "
                "review."
            ),
            (
                "Treat Deployment and post-deploy QA failures as routing signals, not as "
                "automatic terminal sprint outcomes. If Deployment reports an application "
                "runtime/cloud-readiness mismatch, such as local-only persistence, startup "
                "initialization that fails in the target runtime, missing runtime config, or "
                "container/health behavior owned by the app, send the evidence to Fullstack "
                "for repair, then rerun QA if behavior changed and rerun Deployment. If the "
                "failure is Azure resources, registry, secrets, ingress, rollout, or deploy "
                "configuration, rerun Deployment with concrete repair instructions. If QA "
                "finds a deployed behavior defect, route to Fullstack; if QA finds a deployed "
                "environment/config defect, route to Deployment."
            ),
            (
                "Apply coordinator_repair_policy to Fullstack, QA, Deployment, post-deploy "
                "QA, and Handoff tool responses. Inspect failed/blocked/precondition responses "
                "and artifact refs, call codex_review after meaningful downstream work, then "
                "rerun the owning specialist tool with concrete repair advice when repairable. "
                "Use only Team Lead-owned routing between Fullstack, QA, Deployment, and "
                "Handoff for this remediation loop."
            ),
            (
                "After run_handoff returns, read the Handoff Agent response. If you need extra "
                "confidence, call codex_review with the returned handoff artifact refs and ask "
                "only for a lightweight artifact sanity check: report exists, summary exists, "
                "status is clear, and there is no obvious mismatch with the current sprint. Do "
                "not ask Codex Review to perform QA, runtime testing, or deep copy editing."
            ),
            (
                "If Codex review finds meaningful improvements, call run_handoff exactly one "
                "more time with the Codex feedback in message. If Codex review accepts the "
                "handoff, call inspect_sprint_status, then call complete_sprint only if "
                "the inspection readback says can_complete_sprint=true."
            ),
            (
                "Every sprint gets its own sprint handoff. Call run_handoff with "
                "handoff_scope='sprint_handoff' and sprint_id set to the current sprint id. "
                "Keep that sprint handoff request scoped only to the sprint. The sprint handoff "
                "response must return artifact_refs; those refs are the evidence passed "
                "upstream. If the current sprint is the final planned sprint or no later "
                "sprint has pending work, make a separate run_handoff call with "
                "handoff_scope='final_project_report' and an empty sprint_id after the sprint "
                "handoff is accepted. That project/final handoff must also return artifact_refs. "
                "Only call complete_sprint after actual "
                "Handoff-owned sprint/project evidence refs are available and accepted. "
                "Do not create or request wrapper folders, alternate id paths, duplicate reports, "
                "or copied files when the Handoff Agent already returned readable refs. "
                "Head Agent will decide whether to start another sprint or complete the "
                "company run."
            ),
            (
                "Never end the sprint with block_sprint before requesting a sprint-scoped "
                "run_handoff report for the work completed so far. A blocked deployment or "
                "blocked gate still needs a sprint report: completed work, QA evidence, "
                "deployment/preflight evidence, blockers, and next steps. If Handoff cannot "
                "produce the report after a bounded repair attempt, then block the sprint "
                "with that Handoff failure and any available artifact refs. If Handoff is "
                "unavailable but the sprint must stop, ask the most relevant owner, usually "
                "Fullstack for implementation/local-run evidence or Deployment for deployment "
                "evidence, to produce a minimal report artifact before blocking."
            ),
            "If a tool reports it could not act, inspect the response and either send a better "
            "message to the same owner, choose the next useful owner, or stop with evidence "
            "only after the issue is unsafe, needs user approval, or bounded remediation has "
            "been exhausted.",
        ],
    }
    if force_tool_call:
        pending = ", ".join(
            str(item.get("work_item_id"))
            for item in sprint.get("work_items", []) or []
            if str(item.get("status")) in {"todo", "blocked", "in_progress", "review"}
        )
        context["0_critical_directive"] = (
            "Your previous turn produced no tool call. You MUST act by calling a tool now. "
            "This sprint still has pending DB work items"
            + (f": {pending}." if pending else ".")
            + " Call inspect_sprint_status first to read the sprint board, then delegate the "
            "next pending work item to its owner. Do not reply with prose, and do not assume "
            "the sprint is complete from prior-sprint gate fields in delivery_state "
            "(qa_status, deployment_status, public_url may belong to an earlier sprint)."
        )
    return json.dumps(context, indent=2, sort_keys=True)


TEAM_LEAD_SYSTEM_PROMPT = """You are the Team Lead Agent for agentic-company.

You own sprint execution by calling tools. You do not write product code, run QA
directly, deploy directly, or create handoff directly; you delegate to specialist
agents through the tools available to you.

Before delegating work-item work, orient yourself with upstream planning context
when available. Always call `inspect_sprint_status` first; it reads the
independent sprint status inspection output. Treat it as status-only
evidence, not routing advice; you decide the next worker from the DB sprint board
and workflow order. Use `codex_review` as a read-only helper
to inspect BA, architecture, Project Manager, roadmap, and sprint artifacts when
the status or artifact meaning is unclear. Then execute
the selected sprint through specialist tools.

Codex Review is a lightweight read-only helper for Team Lead acceptance of
downstream work. Use it when artifact sanity is not obvious from the tool
response, when planning artifacts are large or conflicting, when expected
artifact refs are missing, when status is unclear, when the result appears to
target the wrong sprint/feature, or when a specialist reports a
blocked/failed/precondition result. Do not force Codex Review for every simple
message exchange, and do not use it as QA, runtime testing, deployment
validation, or a second implementation agent.

Operate like a real team lead:
- review upstream planning artifacts when they are available;
- treat the active sprint package as a DB work item table;
- select the next sprint work item by explicit work_item_id;
- ask the owning specialist to do the work;
- use PM's suggested owner by default, but correct obvious ownership mistakes
  using the current agent registry and role boundaries: Fullstack implements
  product/runtime features, QA verifies, Deployment owns Azure/deployment/
  registry/ingress/rollout/environment readiness, and Handoff owns release
  packaging;
- ask QA to validate implementation/runtime work after owner artifacts exist;
- after every worker or review result, call `inspect_sprint_status` before
  choosing the next worker or terminal tool, but use it only for status,
  evidence, blockers, and completion booleans;
- if QA fails, ask Fullstack to repair with the QA findings and fix request;
- if a specialist reports failed/blocked, inspect the response and artifacts;
  use Codex Review for read-only analysis after meaningful downstream work, then
  rerun the owning specialist only when the issue is repairable and within the
  configured repair limits;
- treat deployment and live-QA failures as Team Lead routing signals. Application
  runtime/cloud-readiness mismatches go to Fullstack with Deployment/QA evidence;
  Azure resources, registry, secrets, ingress, rollout, and deployment
  configuration issues go back to Deployment with concrete repair instructions.
  After the owner repair, rerun the relevant QA/deployment gate instead of
  treating the first failed deploy or live-QA finding as terminal;
- repeat bounded repair loops until every active sprint work item passed QA;
- inspect PM roadmap, sprint policy, and actual delivery state before choosing
  the release gate;
- deploy and run post-deploy QA when the current sprint/release calls for it;
- post-deploy QA must validate the deployed URL itself, including behavior,
  CSS/static asset loading, and obvious layout/style regressions; HTTP 200 alone
  is not enough for handoff;
- for the final planned sprint of an app/site/API/service, assume the normal
  target is a deployed working product URL unless PM/source artifacts explicitly
  say local-only/no-deployment. If PM provides release_gates, roadmap deployment
  rows, deployment-policy text, or a deployment-owned queue item, call
  Deployment Agent before final handoff. The desired final evidence is a web URL
  and any API/internal service URL or resource names. If deployment cannot be
  completed, require Deployment Agent to return exact blocker evidence and then
  include that in handoff rather than silently skipping deployment;
- call Deployment Agent before final handoff when final-sprint deployment
  signals exist;
- treat Azure/dev deployment as supported when it is in scope; call Deployment
  Agent to inspect configured Azure integration and either deploy or return an
  evidenced blocker;
- for local-only/no-deployment sprints, request handoff from local QA evidence
  after active sprint work is done;
- sanity-check the handoff response and returned artifact refs before accepting it;
- create one sprint-scoped handoff for every sprint by calling `run_handoff`
  with `handoff_scope="sprint_handoff"` and `sprint_id` set to the current
  sprint id,
  and treat the returned artifact_refs as the sprint evidence passed upstream;
- on the final planned sprint, make a separate Handoff call with
  `handoff_scope="final_project_report"` and empty `sprint_id` after the sprint
  handoff is accepted, require returned
  artifact_refs for that project handoff, then accept that project handoff too;
- use actual Handoff-owned artifact refs as the contract. Do not request
  duplicate Team Lead wrapper folders, alternate id reports, or copied files merely to
  match an expected path;
- call `complete_sprint` only after the required handoff evidence refs are
  present and accepted and the latest `inspect_sprint_status` readback says
  `can_complete_sprint=true`, so Head receives links to reports rather than
  words only;
- never call `block_sprint` as the first terminal action after a blocked
  deployment/gate. First request a sprint-scoped `run_handoff` report for the
  completed work and the blocker. If Handoff cannot produce it after a bounded
  repair attempt, block with that Handoff failure and any available evidence
  refs. If Handoff is unavailable but the sprint must stop, ask the most relevant
  owner to produce a minimal report artifact before blocking;
- complete or block the sprint.

Specialist tool calls are agent-to-agent communication. Put meaningful downstream
instructions in each tool's `message` field. Read the returned tool response: it
contains the downstream agent's response message and artifacts when available.
The platform automatically attaches the canonical work item packet to specialist
messages. Use that packet and cited artifacts as the contract. Your free-text
message may summarize, prioritize, or ask questions, but it must not silently
add stricter acceptance criteria, exact status codes, feature scope, deployment
gates, or QA gates that are not present in the PM work item or cited artifacts.

Use the `codex_review` tool for read-only artifact sanity checks and Repair
advice when needed. It must not replace the specialist agent that owns
implementation, QA, deployment, or handoff artifacts.

Handoff review protocol:
- after active sprint work and required gates are done, call `run_handoff` for
  `handoff_scope="sprint_handoff"` and the current `sprint_id`;
- if a required gate is blocked, still call `run_handoff` for the current sprint
  scope before `block_sprint`; ask Handoff to produce a blocked/partial sprint
  report with completed work, QA evidence, blocker evidence, and next steps;
- inspect the Handoff Agent response and returned artifact refs;
- if the response is clear and artifacts are present, accept those actual refs;
- for the final planned sprint only, call `run_handoff` again with a separate
  `handoff_scope="final_project_report"` request, inspect/accept that response's
  actual refs, and only then call `inspect_sprint_status` and `complete_sprint`;
- when calling `complete_sprint`, ensure the Team Lead response/result carries
  the accepted handoff artifact refs back to Head Agent;
- if artifacts/status are unclear or mismatched, call `codex_review` with the
  handoff artifact refs and ask for a lightweight sanity check only;
- if the sanity check finds a real gap, call `run_handoff` again with concise
  repair feedback; otherwise accept the handoff.

When a tool says it could not act, adapt and call the correct next tool or send a
better message to the same owner. Block only when the response shows the problem
is not repairable or the configured repair limit has been reached. Keep going
until the sprint is completed or truly blocked.
"""


def langchain_tools(toolbox: TeamLeadToolbox) -> list[Callable[..., str]]:
    def run_fullstack(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate feature implementation, or repair after QA returns findings, to Fullstack."""
        return toolbox.run_fullstack(
            work_item_id,
            reason,
            message,
            artifact_refs,
            external_reference,
        )

    def run_qa(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate QA/review for the explicit sprint work item to the QA Agent."""
        return toolbox.run_qa(work_item_id, reason, message, artifact_refs, external_reference)

    def run_deployment(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate sprint deployment to the Deployment Agent."""
        return toolbox.run_deployment(
            work_item_id,
            reason,
            message,
            artifact_refs,
            external_reference,
        )

    def run_post_deploy_qa(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate post-deployment release QA to the QA Agent."""
        return toolbox.run_post_deploy_qa(
            work_item_id, reason, message, artifact_refs, external_reference
        )

    def run_handoff(
        work_item_id: str,
        handoff_scope: str,
        sprint_id: str = "",
        reason: str = "",
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Delegate handoff packaging using an explicit scope contract.

        Use handoff_scope="sprint_handoff" with sprint_id for sprint reports.
        Use handoff_scope="final_project_report" with empty sprint_id for the
        final project report.
        """
        return toolbox.run_handoff(
            work_item_id,
            handoff_scope,
            sprint_id,
            reason,
            message,
            artifact_refs,
            external_reference,
        )

    def codex_review(
        target_agent: str = "",
        purpose: str = "",
        question: str = "",
        artifact_refs: str = "",
        intent: str = "review_feedback",
        work_item_id: str = "",
        reason: str = "",
        message: str = "",
    ) -> str:
        """Run read-only Codex analysis and send feedback when a target agent is set.

        Pass explicit purpose/question/message and artifact_refs from the relevant
        upstream or downstream tool response.
        """
        return toolbox.codex_review(
            target_agent=target_agent,
            purpose=purpose,
            question=question,
            artifact_refs=artifact_refs,
            intent=intent,
            work_item_id=work_item_id,
            reason=reason,
            message=message,
        )

    def inspect_sprint_status(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        sprint_id: str = "",
        artifact_refs: str = "",
    ) -> str:
        """Run sprint status inspection and read back structured evidence."""
        return toolbox.inspect_sprint_status(
            work_item_id, reason, message, sprint_id, artifact_refs
        )

    def complete_sprint(
        work_item_id: str,
        reason: str = "",
        message: str = "",
        sprint_id: str = "",
        artifact_refs: str = "",
    ) -> str:
        """Mark the sprint complete after inspection confirms readiness."""
        return toolbox.complete_sprint(work_item_id, reason, message, sprint_id, artifact_refs)

    def block_sprint(
        reason: str,
        work_item_id: str,
        message: str = "",
        artifact_refs: str = "",
        external_reference: str = "",
    ) -> str:
        """Block the sprint when progress is impossible or unsafe."""
        return toolbox.block_sprint(
            reason,
            work_item_id,
            message,
            artifact_refs,
            external_reference,
        )

    tools = [
        run_fullstack,
        run_qa,
        run_deployment,
        run_post_deploy_qa,
        run_handoff,
        codex_review,
        inspect_sprint_status,
        complete_sprint,
        block_sprint,
    ]
    for tool in tools:
        contract = TEAM_LEAD_TOOL_CONTRACT_REGISTRY.maybe_get(tool.__name__)
        if contract:
            tool.__doc__ = render_tool_docstring(contract)
    return tools


def _compact_delivery_state(state: DeliveryState) -> dict[str, Any]:
    keys = [
        "run_id",
        "stage",
        "status",
        "qa_status",
        "deployment_status",
        "post_deploy_qa_status",
        "post_deploy_repair_attempts",
        "public_url",
        "public_urls",
        "blockers",
        "max_repair_attempts",
    ]
    return {key: state.get(key) for key in keys if key in state}


def _upstream_planning_context(state: DeliveryState) -> dict[str, Any]:
    refs = _upstream_artifact_refs(state)
    return {
        "artifact_refs": refs,
        "board_source": "db.work_items",
        "guidance": (
            "Use PM artifacts as the execution package, BA artifacts as product scope, "
            "and architecture artifacts as technical constraints."
        ),
    }


def _upstream_artifact_refs(state: DeliveryState) -> list[str]:
    refs = [
        "00-requirements.md",
        *[
            str(artifact.get("path"))
            for artifact in state.get("artifacts", [])
            if artifact.get("kind") == "planning"
            and artifact.get("path")
            and str(artifact.get("path")).startswith("upstream-planning/")
            and "/codex/" not in str(artifact.get("path"))
        ],
    ]
    unique: list[str] = []
    for ref in refs:
        if ref and ref not in unique:
            unique.append(ref)
    return unique
