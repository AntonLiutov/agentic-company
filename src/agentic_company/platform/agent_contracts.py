"""First-class delivery agent contracts and shared state helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol

from agentic_company.platform.agent_runtime import (
    LangChainSpecialistAgentExecutor,
    SpecialistAgentExecutor,
)
from agentic_company.platform.messages import AgentMessageStore, append_agent_response
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import (
    DeliveryState,
    mark_node_completed,
    record_codex_thread,
)

SEND_MESSAGE_TOOL = "send_message"
READ_MESSAGES_TOOL = "read_messages"
READ_ARTIFACT_TOOL = "read_artifact"
SHARE_ARTIFACT_TOOL = "share_artifact"
PUBLISH_ARTIFACT_TOOL = "publish_artifact"
CODEX_EXEC_TOOL = "codex_exec"
CODEX_REVIEW_TOOL = "codex_review"
DELEGATE_TO_AGENT_TOOL = "delegate_to_agent"
REQUEST_HUMAN_APPROVAL_TOOL = "request_human_approval"
COORDINATOR_AGENT_IDS: tuple[str, ...] = (
    "team-lead-agent",
    "project-manager-agent",
    "business-analyst-agent",
    "architect-agent",
    "head-agent",
)
COORDINATOR_RESPONSE_INTENTS: tuple[str, ...] = (
    "report_status",
    "request_clarification",
    "escalate_blocker",
    "agent_response",
)

COMMON_AGENT_TOOLS: tuple[str, ...] = (
    SEND_MESSAGE_TOOL,
    READ_MESSAGES_TOOL,
    READ_ARTIFACT_TOOL,
    SHARE_ARTIFACT_TOOL,
)
CODEX_AGENT_TOOLS: tuple[str, ...] = (CODEX_EXEC_TOOL,)
CODEX_REVIEW_TOOLS: tuple[str, ...] = (CODEX_REVIEW_TOOL,)
COORDINATOR_AGENT_TOOLS: tuple[str, ...] = (
    DELEGATE_TO_AGENT_TOOL,
    REQUEST_HUMAN_APPROVAL_TOOL,
)


@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    """Small metadata record for an agent exposed to orchestration and UI layers."""

    agent_id: str
    name: str
    runtime: str
    stage: str
    family: str = "delivery"


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Tool and runtime permissions granted to one agent role."""

    tools: tuple[str, ...] = field(default_factory=tuple)
    can_use_codex: bool = False
    can_delegate: bool = False
    can_request_human: bool = False

    def allows_tool(self, tool_name: str) -> bool:
        """Return whether this role can use a named platform tool."""

        return tool_name in self.tools


@dataclass(frozen=True, slots=True)
class AgentCommunicationPolicy:
    """Agent-to-agent communication rules for one role.

    The platform can carry messages between any agents, but each role receives a
    scoped policy, similar to IAM permissions. Empty recipient or intent lists
    deny communication by default. Route rules can narrow permissions to a
    specific recipient + intent pair.
    """

    allowed_recipients: tuple[str, ...] = field(default_factory=tuple)
    allowed_intents: tuple[str, ...] = field(default_factory=tuple)
    allowed_routes: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def can_message(self, recipient: str, *, intent: str | None = None) -> bool:
        """Return whether this role may send a message to a recipient."""

        if self.allowed_routes and intent is not None:
            return any(
                _is_allowed(recipient, (route_recipient,)) and _is_allowed(intent, (route_intent,))
                for route_recipient, route_intent in self.allowed_routes
            )
        if not _is_allowed(recipient, self.allowed_recipients):
            return False
        if intent is None:
            return True
        return _is_allowed(intent, self.allowed_intents)


class BaseDeliveryAgent:
    """Base class for state-in/state-out delivery agents.

    The base intentionally owns only platform-level identity, capabilities, and
    communication policy. Domain execution remains in the concrete agent.
    """

    descriptor: ClassVar[AgentDescriptor]
    default_capabilities: ClassVar[AgentCapabilities] = AgentCapabilities()
    default_communication_policy: ClassVar[AgentCommunicationPolicy] = AgentCommunicationPolicy()

    def __init__(
        self,
        *,
        capabilities: AgentCapabilities | None = None,
        communication_policy: AgentCommunicationPolicy | None = None,
    ) -> None:
        self.capabilities = capabilities or self.default_capabilities
        self.communication_policy = communication_policy or self.default_communication_policy

    @property
    def agent_id(self) -> str:
        """Stable id for this agent role."""

        return self.descriptor.agent_id

    def can_use_tool(self, tool_name: str) -> bool:
        """Return whether this agent instance may use a platform tool."""

        return self.capabilities.allows_tool(tool_name)

    def can_message(self, recipient: str, *, intent: str | None = None) -> bool:
        """Return whether this agent instance may message a recipient."""

        return self.communication_policy.can_message(recipient, intent=intent)

    def run(self, state: DeliveryState) -> DeliveryState:
        """Run an agent against delivery state and return updated state."""

        raise NotImplementedError


class BaseAgentExecutorDeliveryAgent(BaseDeliveryAgent):
    """Base class for delivery agents that run through a create_agent executor."""

    def __init__(
        self,
        *,
        agent_executor: SpecialistAgentExecutor | None = None,
        capabilities: AgentCapabilities | None = None,
        communication_policy: AgentCommunicationPolicy | None = None,
    ) -> None:
        super().__init__(
            capabilities=capabilities,
            communication_policy=communication_policy,
        )
        self.agent_executor = agent_executor or LangChainSpecialistAgentExecutor()


class DeliveryAgent(Protocol):
    """State-in/state-out contract for agents composed by the delivery graph."""

    descriptor: AgentDescriptor

    def run(self, state: DeliveryState) -> DeliveryState:
        """Run an agent against delivery state and return updated state."""


def blocked_state(
    state: DeliveryState,
    *,
    node_name: str,
    stage: str,
    reason: str,
) -> DeliveryState:
    """Mark an agent node blocked and preserve the blocker reason in graph state."""

    updated = mark_node_completed(state, node_name=node_name, stage=stage, status="blocked")
    updated["blockers"] = [*state.get("blockers", []), reason]
    return updated


def extend_artifacts(state: DeliveryState, artifacts: list[str]) -> None:
    """Keep run state free of board truth; artifact links live in registry/DB."""

    state["last_artifact_refs"] = [*state.get("last_artifact_refs", []), *artifacts]


def artifact_refs(paths: list[str], *, kind: str = "", owner_agent: str = "") -> list[str]:
    """Return artifact paths for agent messages without deriving registry metadata."""

    return list(paths)


def append_downstream_response(
    state: DeliveryState,
    *,
    from_agent: str,
    result: AgentRunResult,
    default_correlation_id: str | None = None,
    to_agent: str | None = None,
) -> None:
    """Send a generic tool-call response message back to the requesting upstream agent."""

    record_codex_thread(state, result.agent_id or from_agent, result.codex_thread_id)

    parent_message_id = (
        str(state["agent_call_message_id"]) if state.get("agent_call_message_id") else None
    )
    parent_message = (
        AgentMessageStore(state["run_dir"]).get(parent_message_id) if parent_message_id else None
    )
    resolved_to_agent = parent_message.from_agent if parent_message else to_agent
    if not resolved_to_agent:
        return
    correlation_id = (
        parent_message.correlation_id
        if parent_message
        else str(
            state.get("agent_call_correlation_id") or default_correlation_id
            or ""
        )
        or None
    )

    append_agent_response(
        state["run_dir"],
        from_agent=from_agent,
        to_agent=resolved_to_agent,
        status=result.status,
        content=_agent_response_content(result),
        artifact_refs=result.output_artifacts,
        correlation_id=correlation_id,
        parent_message_id=parent_message_id,
        execution_id=result.execution_id or None,
        parent_execution_id=str(state.get("agent_execution_id") or "") or None,
    )


def _agent_response_content(result: AgentRunResult) -> str:
    lines = [result.summary.strip() or f"{result.agent_id} completed with status {result.status}."]
    metadata: list[str] = []
    if result.execution_id:
        metadata.append(f"execution_id: {result.execution_id}")
    if result.codex_thread_id:
        metadata.append(f"codex_thread_id: {result.codex_thread_id}")
    if result.blocking_findings:
        metadata.append(
            "blocking_findings:\n" + "\n".join(f"- {item}" for item in result.blocking_findings)
        )
    if result.fix_request_artifacts:
        metadata.append(
            "fix_request_artifacts:\n"
            + "\n".join(f"- {item}" for item in result.fix_request_artifacts)
        )
    if result.recommended_next_action:
        metadata.append(f"recommended_next_action: {result.recommended_next_action}")
    if metadata:
        lines.append("\nAgent response metadata:\n" + "\n".join(metadata))
    return "\n\n".join(lines)


def target_project_dir(state: DeliveryState) -> Path:
    """Return the target generated-project directory for current delivery state."""

    target_dir = state.get("target_project_dir")
    if target_dir:
        return Path(target_dir)
    return Path(state["run_dir"]) / "generated-project"


def unique_tools(*groups: Sequence[str]) -> tuple[str, ...]:
    """Merge tool groups while preserving first-seen order."""

    tools: list[str] = []
    for group in groups:
        for tool_name in group:
            if tool_name not in tools:
                tools.append(tool_name)
    return tuple(tools)


def codex_delivery_capabilities() -> AgentCapabilities:
    """Return the standard tool grants for Codex-backed delivery specialists."""

    return AgentCapabilities(
        tools=unique_tools(COMMON_AGENT_TOOLS, CODEX_AGENT_TOOLS),
        can_use_codex=True,
    )


def coordinator_response_policy() -> AgentCommunicationPolicy:
    """Return the standard policy for specialists reporting back to coordinators."""

    return AgentCommunicationPolicy(
        allowed_recipients=COORDINATOR_AGENT_IDS,
        allowed_intents=COORDINATOR_RESPONSE_INTENTS,
        allowed_routes=coordinator_response_routes(),
    )


def coordinator_response_routes() -> tuple[tuple[str, str], ...]:
    """Return standard downstream-agent response routes to coordinator roles."""

    return tuple(
        (recipient, intent)
        for recipient in COORDINATOR_AGENT_IDS
        for intent in COORDINATOR_RESPONSE_INTENTS
    )


def _is_allowed(value: str, allowed_values: tuple[str, ...]) -> bool:
    return "*" in allowed_values or value in allowed_values
