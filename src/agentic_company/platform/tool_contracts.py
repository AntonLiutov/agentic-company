"""Typed contracts for tools exposed to AgentExecutors.

The contracts in this module describe how an LLM-facing tool should be called
and how its result can later be stored, rendered in the console, or mirrored to
external delivery dashboards such as GitHub Issues, Jira, or Azure DevOps.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DashboardSystem = Literal["github", "jira", "azure_devops", "internal"]
DashboardReferenceType = Literal["issue", "pull_request", "board_card", "work_item"]
DashboardStatus = Literal["todo", "in_progress", "review", "done", "blocked"]
ToolResultStatus = Literal[
    "succeeded",
    "failed",
    "blocked",
    "needs_repair",
    "human_approval_required",
]


@dataclass(frozen=True, slots=True)
class ArtifactLink:
    """Structured artifact link used by contract-ready tool results."""

    artifact_id: str = ""
    path: str = ""
    label: str = ""
    artifact_type: str = ""
    visibility: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _drop_empty(asdict(self))


@dataclass(frozen=True, slots=True)
class ToolDashboardUpdate:
    """Dashboard-safe status/comment payload for future external integrations."""

    status: DashboardStatus
    summary: str
    comment: str
    artifact_links: tuple[ArtifactLink, ...] = ()
    labels: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_links"] = [artifact.to_dict() for artifact in self.artifact_links]
        data["labels"] = list(self.labels)
        return data


@dataclass(frozen=True, slots=True)
class ToolExternalReference:
    """External dashboard object that a future tool call may update."""

    system: DashboardSystem = "internal"
    type: DashboardReferenceType = "work_item"
    id: str = ""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _drop_empty(asdict(self))


def normalize_external_reference(value: Any) -> dict[str, Any]:
    """Normalize an optional external board reference from tool input."""

    if value is None or value == "":
        return {}
    if isinstance(value, ToolExternalReference):
        reference = value
    else:
        payload: Any = value
        if isinstance(value, str):
            try:
                payload = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("external_reference must be a JSON object string.") from exc
        if not isinstance(payload, dict):
            raise ValueError("external_reference must be a JSON object.")
        allowed = {"system", "type", "id", "url"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                "external_reference contains unsupported fields: " + ", ".join(unknown)
            )
        reference = ToolExternalReference(
            system=str(payload.get("system") or "internal"),  # type: ignore[arg-type]
            type=str(payload.get("type") or "work_item"),  # type: ignore[arg-type]
            id=str(payload.get("id") or ""),
            url=str(payload.get("url") or ""),
        )
    if reference.system not in {"github", "jira", "azure_devops", "internal"}:
        raise ValueError(f"external_reference has unsupported system: {reference.system}")
    if reference.type not in {"issue", "pull_request", "board_card", "work_item"}:
        raise ValueError(f"external_reference has unsupported type: {reference.type}")
    if not reference.id.strip() and not reference.url.strip():
        raise ValueError("external_reference requires id or url.")
    return reference.to_dict()


@dataclass(frozen=True, slots=True)
class WorkItemExecutionPacket:
    """Strict specialist tool input shape for DB-backed work-item execution."""

    run_id: str
    work_item_id: str
    sprint_id: str
    owner_agent: str
    tool_name: str
    tool_call_id: str
    attempt_id: str
    status: str = "in_progress"
    assigned_agent: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _require_contract_fields(
            self,
            (
                "run_id",
                "work_item_id",
                "sprint_id",
                "owner_agent",
                "tool_name",
                "tool_call_id",
                "attempt_id",
                "status",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _drop_empty(asdict(self))


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    """Strict DB write contract for a completed tool transition."""

    run_id: str
    work_item_id: str
    sprint_id: str
    owner_agent: str
    tool_name: str
    tool_call_id: str
    attempt_id: str
    status: str
    activity_message: str
    artifact_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        _require_contract_fields(
            self,
            (
                "run_id",
                "work_item_id",
                "sprint_id",
                "owner_agent",
                "tool_name",
                "tool_call_id",
                "attempt_id",
                "status",
                "activity_message",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["artifact_ids"] = list(self.artifact_ids)
        return _drop_empty(data)


@dataclass(frozen=True, slots=True)
class ActivityEventRecord:
    """Strict DB write contract for user-facing task activity."""

    run_id: str
    event_id: str
    work_item_id: str
    owner_agent: str
    agent_id: str
    tool_name: str
    status: str
    message: str
    artifact_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        _require_contract_fields(
            self,
            (
                "run_id",
                "event_id",
                "work_item_id",
                "owner_agent",
                "agent_id",
                "tool_name",
                "status",
                "message",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["artifact_ids"] = list(self.artifact_ids)
        return _drop_empty(data)


@dataclass(frozen=True, slots=True)
class ArtifactRegistrationRequest:
    """Strict metadata contract for registering a dashboard-visible artifact."""

    artifact_id: str
    artifact_type: str
    visibility: str
    owner_agent: str
    source_tool: str
    label: str
    relative_path: str
    run_id: str
    work_item_id: str = ""
    task_scoped: bool = False

    def validate(self) -> None:
        _require_contract_fields(
            self,
            (
                "artifact_id",
                "artifact_type",
                "visibility",
                "owner_agent",
                "source_tool",
                "label",
                "relative_path",
                "run_id",
            ),
        )
        if self.task_scoped and not self.work_item_id.strip():
            raise ValueError("Missing required contract fields: work_item_id")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _drop_empty(asdict(self))


@dataclass(frozen=True, slots=True)
class ToolCallInput:
    """DB-ready shape for a tool invocation.

    Runtime tools should be invoked through explicit contract fields.
    """

    tool_name: str
    tool_call_id: str = ""
    project_id: str = ""
    run_id: str = ""
    agent_id: str = ""
    work_item_id: str = ""
    sprint_id: str = ""
    artifact_refs: tuple[ArtifactLink, ...] = ()
    external_reference: ToolExternalReference | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifact_refs"] = [artifact.to_dict() for artifact in self.artifact_refs]
        data["external_reference"] = (
            self.external_reference.to_dict() if self.external_reference else None
        )
        return _drop_empty(data)


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """DB-ready, dashboard-ready shape for a tool response."""

    tool_name: str
    status: str
    business_summary: str
    tool_call_id: str = ""
    developer_diagnostics: dict[str, Any] = field(default_factory=dict)
    output_artifacts: tuple[ArtifactLink, ...] = ()
    failure_mode: str | None = None
    recommended_next_action: str = ""
    dashboard_update: ToolDashboardUpdate | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["output_artifacts"] = [artifact.to_dict() for artifact in self.output_artifacts]
        data["dashboard_update"] = (
            self.dashboard_update.to_dict() if self.dashboard_update else None
        )
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Human, model, and test-readable contract for one AgentExecutor tool."""

    tool_name: str
    owner_agent: str
    purpose: str
    business_description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_parameters: tuple[str, ...]
    optional_parameters: tuple[str, ...]
    artifact_inputs: tuple[str, ...]
    artifact_outputs: tuple[str, ...]
    status_outputs: tuple[str, ...]
    failure_modes: tuple[str, ...]
    retry_policy: str
    idempotency: str
    examples: tuple[dict[str, Any], ...]
    external_reference_type: str = "work_item"
    external_reference_id: str = ""
    dashboard_status: str = "in_progress"
    dashboard_summary: str = ""
    dashboard_comment: str = ""
    dashboard_artifact_links: tuple[str, ...] = ()
    risk_level: str = "medium"
    requires_human_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolContractRegistry:
    """Small immutable registry for AgentExecutor tool contracts."""

    def __init__(self, contracts: list[ToolContract] | tuple[ToolContract, ...]) -> None:
        self._contracts = {contract.tool_name: contract for contract in contracts}

    def get(self, tool_name: str) -> ToolContract:
        return self._contracts[tool_name]

    def maybe_get(self, tool_name: str) -> ToolContract | None:
        return self._contracts.get(tool_name)

    def all(self) -> tuple[ToolContract, ...]:
        return tuple(self._contracts.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._contracts)


def render_tool_docstring(contract: ToolContract) -> str:
    """Render an LLM-facing docstring from a tool contract."""

    required = ", ".join(contract.required_parameters) or "none"
    optional = ", ".join(contract.optional_parameters) or "none"
    statuses = ", ".join(contract.status_outputs)
    failures = ", ".join(contract.failure_modes)
    artifact_inputs = ", ".join(contract.artifact_inputs) or "none"
    artifact_outputs = ", ".join(contract.artifact_outputs) or "none"
    example = json.dumps(contract.examples[0] if contract.examples else {}, sort_keys=True)
    external_reference_note = (
        "External dashboard support: pass external_reference when mirroring this work "
        "to GitHub, Jira, Azure DevOps, or the internal board.\n"
        if "external_reference" in contract.required_parameters
        or "external_reference" in contract.optional_parameters
        else ""
    )
    return (
        f"{contract.purpose}\n\n"
        f"Business use: {contract.business_description}\n"
        f"Required parameters: {required}.\n"
        f"Optional parameters: {optional}.\n"
        f"Preferred artifact inputs: {artifact_inputs}.\n"
        f"Expected artifact outputs: {artifact_outputs}.\n"
        f"Possible statuses: {statuses}.\n"
        f"Failure modes: {failures}.\n"
        f"Retry policy: {contract.retry_policy}\n"
        f"Idempotency: {contract.idempotency}\n"
        f"{external_reference_note}"
        f"Dashboard status mapping: {contract.dashboard_status}.\n"
        f"Example call: {example}."
    )


CODEX_EXEC_TOOL_CONTRACT = ToolContract(
    tool_name="codex_exec",
    owner_agent="specialist-agent",
    purpose="Run the assigned Codex-backed specialist worker for the current task.",
    business_description="Specialist executes the assigned delivery task and returns artifacts.",
    input_schema={
        "reason": "string AgentExecutor rationale",
        "message": "string repair or execution instruction",
        "artifact_refs": "array of registered artifact ids from prior result",
    },
    output_schema={
        "tool_call_id": "string",
        "tool_name": "string",
        "status": "string",
        "business_summary": "string",
        "developer_diagnostics": "object",
        "output_artifacts": "array",
        "failure_mode": "string|null",
        "recommended_next_action": "string",
        "dashboard_update": "object",
    },
    required_parameters=("reason",),
    optional_parameters=("message", "artifact_refs"),
    artifact_inputs=("execution_request", "previous_result_artifacts"),
    artifact_outputs=("worker_artifacts", "fix_request_artifacts", "agent_response"),
    status_outputs=("succeeded", "failed", "blocked", "needs_repair"),
    failure_modes=("codex_failed", "tool_call_limit_reached", "provider_limit", "blocked"),
    retry_policy="Retry only when previous result is repairable and tool limit remains.",
    idempotency="Not idempotent; may edit generated project files or produce new artifacts.",
    examples=(
        {
            "reason": "Implement assigned feature.",
            "message": "Use the execution request and report artifacts when complete.",
        },
    ),
    external_reference_type="work_item",
    dashboard_status="in_progress",
    dashboard_summary="Specialist executes the assigned delivery task.",
    dashboard_comment="Specialist execution started or updated.",
    risk_level="high",
)


def dashboard_status_from_runtime_status(status: str) -> DashboardStatus:
    """Map internal runtime statuses to dashboard-friendly board statuses."""

    normalized = status.strip().lower()
    if any(token in normalized for token in ("blocked", "failed", "precondition", "needs_repair")):
        return "blocked"
    if any(token in normalized for token in ("ready", "done", "completed", "deployed")):
        return "done"
    if any(token in normalized for token in ("qa", "review", "inspect")):
        return "review"
    if normalized in {"pending", "todo"}:
        return "todo"
    return "in_progress"


def failure_mode_from_status(status: str, blockers: list[Any] | tuple[Any, ...] = ()) -> str | None:
    """Return a machine-readable failure mode for a runtime status when obvious."""

    normalized = status.strip().lower()
    blocker_text = " ".join(str(blocker).lower() for blocker in blockers)
    provider_limit_text = f"{normalized} {blocker_text}"
    if any(
        token in provider_limit_text
        for token in (
            "provider_limit",
            "usage_limit",
            "usage limit",
            "quota",
            "rate_limit",
            "rate limit",
            "purchase more credits",
            "capacity",
        )
    ):
        return "provider_limit"
    if "human" in normalized or "approval" in normalized:
        return "human_approval_required"
    if "needs_repair" in normalized or "qa_failed" in normalized:
        return "needs_repair"
    if any(token in normalized for token in ("blocked", "failed", "error", "precondition")):
        return "failed"
    if any(
        token in normalized
        for token in ("ready", "done", "completed", "deployed", "passed", "implemented")
    ):
        return None
    if blockers:
        return "blocked"
    return None


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in ("", None, [], {}, ())}


def _require_contract_fields(instance: object, fields: tuple[str, ...]) -> None:
    missing = [
        field_name
        for field_name in fields
        if not str(getattr(instance, field_name, "") or "").strip()
    ]
    if missing:
        raise ValueError("Missing required contract fields: " + ", ".join(missing))
