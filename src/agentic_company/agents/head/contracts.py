"""Head Agent tool contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from agentic_company.platform.contracts.tool_contracts import ToolContract, ToolContractRegistry

HeadToolName = Literal[
    "run_business_analyst",
    "run_architect",
    "run_project_manager",
    "run_team_lead",
    "codex_review",
    "inspect_delivery_status",
    "complete_delivery",
    "block_planning",
]

HEAD_TOOLS: tuple[HeadToolName, ...] = (
    "run_business_analyst",
    "run_architect",
    "run_project_manager",
    "run_team_lead",
    "codex_review",
    "inspect_delivery_status",
    "complete_delivery",
    "block_planning",
)

COMMON_RESULT_SCHEMA: dict[str, object] = {
    "tool_call_id": "string",
    "tool_name": "string",
    "status": "string",
    "business_summary": "string",
    "developer_diagnostics": "object",
    "output_artifacts": "array",
    "failure_mode": "string|null",
    "recommended_next_action": "string",
    "dashboard_update": "object",
}

HEAD_COORDINATOR_INPUT_SCHEMA: dict[str, object] = {
    "work_item_id": "explicit DB work item id when the tool is work-item scoped",
    "sprint_id": "explicit sprint id when the tool is sprint-scoped",
    "reason": "string coordinator rationale",
    "message": "string downstream assignment or decision context",
    "artifact_refs": "array of registered artifact ids",
    "external_reference": "optional GitHub/Jira/Azure/internal dashboard reference",
}


def _contract(
    *,
    tool_name: str,
    purpose: str,
    business_description: str,
    required_parameters: tuple[str, ...],
    optional_parameters: tuple[str, ...],
    artifact_inputs: tuple[str, ...],
    artifact_outputs: tuple[str, ...],
    status_outputs: tuple[str, ...],
    failure_modes: tuple[str, ...],
    retry_policy: str,
    idempotency: str,
    examples: tuple[dict[str, object], ...],
    dashboard_status: str,
    risk_level: str = "medium",
    input_schema: dict[str, object] | None = None,
) -> ToolContract:
    return ToolContract(
        tool_name=tool_name,
        owner_agent="head-agent",
        purpose=purpose,
        business_description=business_description,
        input_schema=input_schema or HEAD_COORDINATOR_INPUT_SCHEMA,
        output_schema=COMMON_RESULT_SCHEMA,
        required_parameters=required_parameters,
        optional_parameters=optional_parameters,
        artifact_inputs=artifact_inputs,
        artifact_outputs=artifact_outputs,
        status_outputs=status_outputs,
        failure_modes=failure_modes,
        retry_policy=retry_policy,
        idempotency=idempotency,
        examples=examples,
        external_reference_type="work_item",
        dashboard_status=dashboard_status,
        dashboard_summary=business_description,
        dashboard_comment=business_description,
        risk_level=risk_level,
    )


HEAD_TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    _contract(
        tool_name="run_business_analyst",
        purpose="Delegate source requirement analysis to the Business Analyst agent.",
        business_description="Business Analyst converts the product idea into a delivery brief.",
        required_parameters=("reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("requirements_brief", "source_request"),
        artifact_outputs=("business_analysis", "agent_response", "head_decision"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("worker_failed", "contract_gap", "provider_limit", "blocked"),
        retry_policy="Retry only with concrete review findings or missing brief details.",
        idempotency="Not idempotent; may rewrite planning artifacts and messages.",
        dashboard_status="in_progress",
        examples=(
            {
                "reason": "Clarify product intent.",
                "message": "Analyze the request and return business-readable artifact refs.",
            },
        ),
    ),
    _contract(
        tool_name="run_architect",
        purpose="Delegate solution architecture after Business Analyst artifacts are ready.",
        business_description=(
            "Solution Architect turns the delivery brief into a buildable approach."
        ),
        required_parameters=("reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("requirements_brief", "business_analysis"),
        artifact_outputs=("architecture_report", "architecture_json", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("worker_failed", "business_analysis_missing", "contract_gap", "blocked"),
        retry_policy="Retry after BA artifacts or review feedback identify a concrete gap.",
        idempotency="Not idempotent; may rewrite architecture artifacts.",
        dashboard_status="in_progress",
        examples=(
            {
                "reason": "Create build approach.",
                "message": "Use BA refs and return concise architecture artifacts for planning.",
            },
        ),
    ),
    _contract(
        tool_name="run_project_manager",
        purpose="Delegate release and sprint planning after architecture is ready.",
        business_description=(
            "Project Manager creates a Team Lead-consumable planned work item contract."
        ),
        required_parameters=("reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("business_analysis", "architecture_report"),
        artifact_outputs=("release_plan", "sprint_plan", "planned_work_items"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("worker_failed", "architecture_missing", "invalid_work_items"),
        retry_policy="Retry when sprint ids, queue shape, or acceptance criteria are incomplete.",
        idempotency="Not idempotent; may rewrite release and sprint plans.",
        dashboard_status="in_progress",
        examples=(
            {
                "reason": "Plan execution.",
                "message": (
                    "Create the smallest useful sprint plan and canonical planned work "
                    "item contract."
                ),
            },
        ),
    ),
    _contract(
        tool_name="run_team_lead",
        purpose="Delegate one explicit PM-planned sprint to Team Lead.",
        business_description=(
            "Team Lead executes planned sprint work through builder, review, deploy, and handoff."
        ),
        required_parameters=("sprint_id", "reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("release_plan", "sprint_plan", "planned_work_items"),
        artifact_outputs=("team_lead_result", "handoff_artifacts", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("missing_sprint_target", "worker_failed", "delivery_blocked"),
        retry_policy=(
            "Retry with the same sprint only after concrete repair or missing evidence feedback."
        ),
        idempotency="State-changing; do not rerun completed sprints without new evidence.",
        dashboard_status="in_progress",
        risk_level="high",
        examples=(
            {
                "sprint_id": "sprint-01",
                "reason": "Execute planned sprint.",
                "message": "Use the canonical sprint plan and return delivery evidence refs.",
            },
        ),
    ),
    _contract(
        tool_name="codex_review",
        purpose="Run advisory read-only Codex review of referenced planning or delivery artifacts.",
        business_description=(
            "Head reviews returned evidence before routing repair or continuing delivery."
        ),
        required_parameters=("purpose", "question", "artifact_refs"),
        optional_parameters=("target_agent", "intent", "correlation_id", "reason", "message"),
        artifact_inputs=("referenced_artifacts",),
        artifact_outputs=("review_summary", "review_prompt", "review_log"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("review_failed", "artifact_unavailable", "tool_limit_reached"),
        retry_policy="Retry only when artifact refs or review question were incomplete.",
        idempotency="Read-only with new review artifacts written per execution.",
        dashboard_status="review",
        examples=(
            {
                "purpose": "Review BA readiness.",
                "question": "Is this ready for architecture?",
                "artifact_refs": "upstream-planning/business-analysis.md",
            },
        ),
    ),
    _contract(
        tool_name="inspect_delivery_status",
        purpose="Inspect delivery board, gates, artifacts, blockers, and completion readiness.",
        business_description=(
            "Status Inspector provides a machine-readable delivery progress readback."
        ),
        required_parameters=("reason",),
        optional_parameters=("message", "artifact_refs"),
        artifact_inputs=("planning_artifacts", "team_lead_history", "handoff_artifacts"),
        artifact_outputs=("status_inspection_json", "status_summary", "status_logs"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("inspection_failed", "artifact_unavailable", "tool_limit_reached"),
        retry_policy=(
            "Safe to retry after new planning, delivery, deployment, or handoff evidence appears."
        ),
        idempotency="Read-only except for status inspection artifacts.",
        dashboard_status="review",
        examples=(
            {
                "correlation_id": "company-delivery",
                "reason": "Confirm delivery completion readiness.",
                "message": "Inspect status only; Head owns routing.",
            },
        ),
    ),
    _contract(
        tool_name="complete_delivery",
        purpose="Mark the company delivery run complete after inspector confirms readiness.",
        business_description=(
            "Head closes the run after planning, delivery, and handoff evidence are complete."
        ),
        required_parameters=("reason",),
        optional_parameters=("message", "artifact_refs"),
        artifact_inputs=("status_inspection_json", "handoff_artifacts", "team_lead_result"),
        artifact_outputs=("head_result", "completion_event"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("completion_not_ready", "handoff_missing", "tool_limit_reached"),
        retry_policy="Retry only after inspector or handoff evidence proves completion readiness.",
        idempotency="State-changing; repeated calls should be avoided.",
        dashboard_status="done",
        examples=(
            {
                "reason": "Inspector confirms can_complete_delivery.",
                "message": "Complete delivery with accepted handoff evidence.",
            },
        ),
    ),
    _contract(
        tool_name="block_planning",
        purpose="Block upstream planning or delivery when bounded Repair cannot continue.",
        business_description=(
            "Head records a visible blocker instead of hiding a failed automation step."
        ),
        required_parameters=("reason",),
        optional_parameters=("correlation_id", "message", "artifact_refs", "external_reference"),
        artifact_inputs=("blocker_evidence",),
        artifact_outputs=("block_event", "head_result"),
        status_outputs=("blocked",),
        failure_modes=("blocked",),
        retry_policy="Do not retry block_planning; unblock requires operator action or a new run.",
        idempotency="State-changing; repeated calls append blockers.",
        dashboard_status="blocked",
        risk_level="high",
        examples=(
            {
                "correlation_id": "upstream-planning",
                "reason": "Required provider key is missing.",
                "message": "Block with exact next action for the user.",
            },
        ),
    ),
)

HEAD_TOOL_CONTRACT_REGISTRY = ToolContractRegistry(HEAD_TOOL_CONTRACTS)


@dataclass(frozen=True, slots=True)
class HeadDecision:
    """Recorded Head Agent tool-call decision."""

    tool: HeadToolName
    reason: str
    correlation_id: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
