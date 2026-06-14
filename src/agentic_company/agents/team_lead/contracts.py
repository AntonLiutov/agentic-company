"""Team Lead tool contracts and runtime environment helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from agentic_company.platform.agent_runtime import agent_env_value
from agentic_company.platform.state import DeliveryState
from agentic_company.platform.tool_contracts import (
    CODEX_EXEC_TOOL_CONTRACT,
    ToolContract,
    ToolContractRegistry,
)

TeamLeadToolName = Literal[
    "run_fullstack",
    "run_qa",
    "run_deployment",
    "run_post_deploy_qa",
    "run_handoff",
    "codex_review",
    "inspect_sprint_status",
    "complete_sprint",
    "block_sprint",
]

TEAM_LEAD_TOOLS: tuple[TeamLeadToolName, ...] = (
    "run_fullstack",
    "run_qa",
    "run_deployment",
    "run_post_deploy_qa",
    "run_handoff",
    "codex_review",
    "inspect_sprint_status",
    "complete_sprint",
    "block_sprint",
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

WORK_ITEM_INPUT_SCHEMA: dict[str, object] = {
    "work_item_id": "required explicit DB work item id",
    "reason": "string coordinator rationale",
    "message": "string downstream instruction",
    "artifact_refs": "array of registered artifact ids",
    "external_reference": "optional GitHub/Jira/Azure/internal dashboard reference",
}


def _contract(
    *,
    tool_name: str,
    owner_agent: str,
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
        owner_agent=owner_agent,
        purpose=purpose,
        business_description=business_description,
        input_schema=input_schema or WORK_ITEM_INPUT_SCHEMA,
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
        dashboard_status=dashboard_status,
        dashboard_summary=business_description,
        dashboard_comment=business_description,
        external_reference_type="work_item",
        risk_level=risk_level,
    )


TEAM_LEAD_TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    _contract(
        tool_name="run_fullstack",
        owner_agent="team-lead-agent",
        purpose="Delegate product/runtime implementation or repair to the Fullstack agent.",
        business_description="Builder implements or repairs the selected sprint work item.",
        required_parameters=("work_item_id", "reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("planning_artifacts", "qa_fix_request_artifacts"),
        artifact_outputs=("execution_artifacts", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("missing_work_item_id", "repair_limit_reached", "worker_failed"),
        retry_policy="Retry only with new QA/review findings or concrete repair guidance.",
        idempotency="Not idempotent; may create or modify generated application files.",
        dashboard_status="in_progress",
        examples=(
            {
                "work_item_id": "US-01",
                "reason": "Implement planned sprint item.",
                "message": "Build the selected feature using the canonical work item packet.",
            },
        ),
    ),
    _contract(
        tool_name="run_qa",
        owner_agent="team-lead-agent",
        purpose="Delegate validation of a selected sprint work item to the QA agent.",
        business_description="Quality Reviewer checks behavior, styling, and release confidence.",
        required_parameters=("work_item_id", "reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("execution_artifacts", "planning_artifacts"),
        artifact_outputs=("qa_evidence", "fix_request_artifacts", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("missing_work_item_id", "repair_limit_reached", "qa_failed"),
        retry_policy="Retry after Fullstack or Deployment repairs the cited findings.",
        idempotency="Mostly idempotent for read-only checks, but may write QA evidence artifacts.",
        dashboard_status="review",
        examples=(
            {
                "work_item_id": "US-01",
                "reason": "Validate implementation artifacts.",
                "message": "Test the selected feature and return clear evidence or fix requests.",
            },
        ),
    ),
    _contract(
        tool_name="run_deployment",
        owner_agent="team-lead-agent",
        purpose="Delegate sprint deployment or deployment repair to the Deployment agent.",
        business_description="Publisher prepares a reachable deployed demo or reports blockers.",
        required_parameters=("work_item_id", "reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("qa_evidence", "execution_artifacts", "deployment_policy"),
        artifact_outputs=("deployment_evidence", "public_url", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("deploy_failed", "secret_missing", "provider_limit", "environment_blocked"),
        retry_policy="Retry after app/runtime or infrastructure repair with new evidence.",
        idempotency="Not guaranteed; may update cloud resources or deployment state.",
        dashboard_status="in_progress",
        risk_level="high",
        examples=(
            {
                "work_item_id": "US-deployment",
                "reason": "Deploy sprint after QA passed.",
                "message": "Deploy the current sprint and return URL or exact blocker evidence.",
            },
        ),
    ),
    _contract(
        tool_name="run_post_deploy_qa",
        owner_agent="team-lead-agent",
        purpose="Delegate live deployed runtime validation to the QA agent.",
        business_description="Quality Reviewer validates the public deployment before handoff.",
        required_parameters=("work_item_id", "reason", "message"),
        optional_parameters=("artifact_refs", "external_reference"),
        artifact_inputs=("deployment_evidence", "public_url", "qa_evidence"),
        artifact_outputs=("post_deploy_qa_evidence", "fix_request_artifacts", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("live_qa_failed", "public_url_missing", "repair_limit_reached"),
        retry_policy="Retry after owner repair and redeployment when needed.",
        idempotency="Mostly idempotent for read-only checks, but writes QA evidence artifacts.",
        dashboard_status="review",
        examples=(
            {
                "work_item_id": "US-deployment",
                "reason": "Validate deployed sprint.",
                "message": (
                    "Open the public URL and verify delivered behavior, CSS/static asset "
                    "loading, and obvious layout/style regressions before handoff."
                ),
            },
        ),
    ),
    _contract(
        tool_name="run_handoff",
        owner_agent="team-lead-agent",
        purpose="Delegate sprint or final project report packaging to the Handoff agent.",
        business_description="Release Reporter prepares stakeholder-readable delivery evidence.",
        required_parameters=("work_item_id", "handoff_scope", "reason", "message"),
        optional_parameters=("sprint_id", "artifact_refs", "external_reference"),
        artifact_inputs=("execution_artifacts", "qa_evidence", "deployment_evidence"),
        artifact_outputs=("release_report", "handoff_artifacts", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked", "human_approval_required"),
        failure_modes=("invalid_handoff_scope", "missing_handoff_evidence", "handoff_failed"),
        retry_policy="Retry once with concrete missing evidence or review feedback.",
        idempotency="Not fully idempotent; may rewrite handoff artifacts for the same scope.",
        dashboard_status="review",
        examples=(
            {
                "work_item_id": "PLAN-04",
                "handoff_scope": "sprint_handoff",
                "sprint_id": "sprint-01",
                "reason": "Sprint evidence is ready.",
                "message": "Create a stakeholder-readable sprint report.",
            },
        ),
    ),
    _contract(
        tool_name="codex_review",
        owner_agent="team-lead-agent",
        purpose="Run read-only Codex review of artifacts for orientation or Repair advice.",
        business_description=(
            "Read-only reviewer checks referenced artifacts and gives concise advice."
        ),
        required_parameters=("work_item_id", "purpose", "question", "artifact_refs"),
        optional_parameters=("target_agent", "intent", "reason", "message"),
        artifact_inputs=("referenced_artifacts",),
        artifact_outputs=("review_summary", "review_prompt", "review_log", "agent_response"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("review_failed", "artifact_unavailable", "tool_limit_reached"),
        retry_policy="Retry only when artifact refs or question were incomplete.",
        idempotency="Read-only with new review artifacts written per execution.",
        dashboard_status="in_progress",
        examples=(
            {
                "work_item_id": "PLAN-04",
                "purpose": "Review handoff readiness.",
                "question": "Does the report match the completed sprint evidence?",
                "artifact_refs": "handoff/sprints/sprint-01/release-report.html",
            },
        ),
    ),
    _contract(
        tool_name="inspect_sprint_status",
        owner_agent="team-lead-agent",
        purpose="Inspect sprint board, gates, blockers, evidence, and completion readiness.",
        business_description=(
            "Status inspector gives Team Lead a readback of progress and blockers."
        ),
        required_parameters=("work_item_id", "reason"),
        optional_parameters=("message", "sprint_id", "artifact_refs"),
        artifact_inputs=("planning_artifacts", "history_artifacts", "handoff_artifacts"),
        artifact_outputs=("status_inspection_json", "status_summary", "status_logs"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("inspection_failed", "artifact_unavailable", "tool_limit_reached"),
        retry_policy="Safe to retry after new worker, QA, deployment, or handoff evidence appears.",
        idempotency="Read-only except for status inspection artifacts.",
        dashboard_status="in_progress",
        examples=(
            {
                "work_item_id": "PLAN-04",
                "reason": "Confirm sprint completion readiness.",
                "message": "Inspect gates and evidence only; do not choose routing.",
            },
        ),
    ),
    _contract(
        tool_name="complete_sprint",
        owner_agent="team-lead-agent",
        purpose="Mark sprint complete after handoff evidence and status inspection are accepted.",
        business_description="Team Lead closes the sprint and passes evidence upstream.",
        required_parameters=("work_item_id", "reason"),
        optional_parameters=("message", "sprint_id", "artifact_refs"),
        artifact_inputs=("handoff_artifacts", "status_inspection_json"),
        artifact_outputs=("team_lead_result", "completion_event"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("handoff_missing", "completion_not_ready", "tool_limit_reached"),
        retry_policy="Retry only after missing handoff/status evidence is produced.",
        idempotency="State-changing; do not call repeatedly without new evidence.",
        dashboard_status="done",
        examples=(
            {
                "work_item_id": "PLAN-04",
                "reason": "Handoff evidence accepted and can_complete_sprint is true.",
                "message": "Complete the sprint with accepted artifact refs.",
            },
        ),
    ),
    _contract(
        tool_name="block_sprint",
        owner_agent="team-lead-agent",
        purpose="Block sprint only after bounded remediation and report/evidence attempts.",
        business_description=(
            "Team Lead records a visible blocker when delivery cannot continue safely."
        ),
        required_parameters=("work_item_id", "reason"),
        optional_parameters=("message", "artifact_refs", "external_reference"),
        artifact_inputs=("blocker_evidence",),
        artifact_outputs=("block_event", "team_lead_result"),
        status_outputs=("blocked",),
        failure_modes=("blocked",),
        retry_policy="Do not retry block_sprint; unblock requires human/operator action.",
        idempotency="State-changing; repeated calls append blockers.",
        dashboard_status="blocked",
        risk_level="high",
        examples=(
            {
                "work_item_id": "PLAN-04",
                "reason": "Deployment cannot proceed because required secret is missing.",
                "message": "Block with exact evidence and next step.",
            },
        ),
    ),
)

RUNNER_TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    CODEX_EXEC_TOOL_CONTRACT,
    _contract(
        tool_name="deployment_runner",
        owner_agent="deployment-agent",
        purpose="Internal runner that performs deployment work for Deployment agent.",
        business_description="Deployment runtime publishes the generated demo or returns blockers.",
        required_parameters=("run_id", "target_project_dir"),
        optional_parameters=("artifact_refs", "environment"),
        artifact_inputs=("deployment_request", "generated_project"),
        artifact_outputs=("deployment_evidence", "public_url"),
        status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
        failure_modes=("deploy_failed", "secret_missing", "cloud_unavailable"),
        retry_policy="Retry after deployment or app runtime repair.",
        idempotency="Not idempotent; may create or update cloud resources.",
        dashboard_status="in_progress",
        risk_level="high",
        examples=({"run_id": "run-1", "target_project_dir": "generated-project"},),
    ),
    _contract(
        tool_name="handoff_report_runner",
        owner_agent="documentation-handoff-agent",
        purpose="Internal runner that prepares sprint or final business reports.",
        business_description="Report runtime creates stakeholder-readable handoff artifacts.",
        required_parameters=("handoff_scope",),
        optional_parameters=("sprint_id", "artifact_refs"),
        artifact_inputs=("execution_artifacts", "qa_evidence", "deployment_evidence"),
        artifact_outputs=("release_report", "handoff_json", "handoff_markdown"),
        status_outputs=("succeeded", "failed", "blocked"),
        failure_modes=("invalid_handoff_scope", "missing_evidence", "report_failed"),
        retry_policy="Retry after missing evidence is supplied.",
        idempotency="May overwrite report artifacts for the same scope.",
        dashboard_status="review",
        examples=({"handoff_scope": "final_project_report", "sprint_id": ""},),
    ),
)

CRITICAL_TOOL_CONTRACTS: tuple[ToolContract, ...] = (
    *TEAM_LEAD_TOOL_CONTRACTS,
    *RUNNER_TOOL_CONTRACTS,
)

TEAM_LEAD_TOOL_CONTRACT_REGISTRY = ToolContractRegistry(TEAM_LEAD_TOOL_CONTRACTS)
CRITICAL_TOOL_CONTRACT_REGISTRY = ToolContractRegistry(CRITICAL_TOOL_CONTRACTS)


@dataclass(frozen=True, slots=True)
class TeamLeadDecision:
    """Recorded Team Lead tool call decision."""

    tool: TeamLeadToolName
    reason: str
    work_item_id: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def env_value(key: str, delivery_state: DeliveryState) -> str:
    """Read runtime config from process env, run env, or repo env."""

    return agent_env_value(key, delivery_state)
