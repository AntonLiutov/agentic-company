"""First-class Head Agent wrapper."""

from __future__ import annotations

from agentic_company.agents.architecture.agent import ArchitectAgent
from agentic_company.agents.business_analysis.agent import BusinessAnalystAgent
from agentic_company.agents.head.contracts import HEAD_TOOLS
from agentic_company.agents.head.graph import HeadExecutor, run_head_agent_graph
from agentic_company.agents.head.tools import HeadWorkers
from agentic_company.agents.project_manager.agent import ProjectManagerAgent
from agentic_company.agents.team_lead.agent import TeamLeadAgent
from agentic_company.platform.agent_contracts import (
    CODEX_REVIEW_TOOLS,
    COMMON_AGENT_TOOLS,
    COORDINATOR_AGENT_TOOLS,
    AgentCapabilities,
    AgentCommunicationPolicy,
    AgentDescriptor,
    BaseDeliveryAgent,
    unique_tools,
)
from agentic_company.platform.state import DeliveryState

HEAD_ALLOWED_RECIPIENTS: tuple[str, ...] = (
    "business-analyst-agent",
    "architect-agent",
    "project-manager-agent",
    "team-lead-agent",
)

HEAD_ALLOWED_MESSAGE_INTENTS: tuple[str, ...] = (
    "request_business_analysis",
    "request_architecture",
    "request_project_management",
    "request_sprint_delivery",
    "report_status",
    "request_clarification",
    "escalate_blocker",
    "agent_response",
)

HEAD_ALLOWED_MESSAGE_ROUTES: tuple[tuple[str, str], ...] = (
    ("business-analyst-agent", "request_business_analysis"),
    ("business-analyst-agent", "report_status"),
    ("business-analyst-agent", "request_clarification"),
    ("business-analyst-agent", "escalate_blocker"),
    ("business-analyst-agent", "agent_response"),
    ("architect-agent", "request_architecture"),
    ("architect-agent", "report_status"),
    ("architect-agent", "request_clarification"),
    ("architect-agent", "escalate_blocker"),
    ("architect-agent", "agent_response"),
    ("project-manager-agent", "request_project_management"),
    ("project-manager-agent", "report_status"),
    ("project-manager-agent", "request_clarification"),
    ("project-manager-agent", "escalate_blocker"),
    ("project-manager-agent", "agent_response"),
    ("team-lead-agent", "request_sprint_delivery"),
    ("team-lead-agent", "report_status"),
    ("team-lead-agent", "request_clarification"),
    ("team-lead-agent", "escalate_blocker"),
    ("team-lead-agent", "agent_response"),
)


class HeadAgent(BaseDeliveryAgent):
    """Coordinate upstream planning specialists before delivery execution."""

    descriptor = AgentDescriptor(
        agent_id="head-agent",
        name="Head Agent",
        runtime="L4 LangGraph Agent Executor",
        stage="head",
        family="company-coordination",
    )
    default_capabilities = AgentCapabilities(
        tools=unique_tools(
            COMMON_AGENT_TOOLS,
            COORDINATOR_AGENT_TOOLS,
            CODEX_REVIEW_TOOLS,
            HEAD_TOOLS,
        ),
        can_delegate=True,
        can_request_human=True,
    )
    default_communication_policy = AgentCommunicationPolicy(
        allowed_recipients=HEAD_ALLOWED_RECIPIENTS,
        allowed_intents=HEAD_ALLOWED_MESSAGE_INTENTS,
        allowed_routes=HEAD_ALLOWED_MESSAGE_ROUTES,
    )

    def __init__(
        self,
        workers: HeadWorkers | None = None,
        executor: HeadExecutor | None = None,
        *,
        capabilities: AgentCapabilities | None = None,
        communication_policy: AgentCommunicationPolicy | None = None,
    ) -> None:
        super().__init__(
            capabilities=capabilities,
            communication_policy=communication_policy,
        )
        self.workers = workers or HeadWorkers(
            business_analyst=BusinessAnalystAgent().run,
            architect=ArchitectAgent().run,
            project_manager=ProjectManagerAgent().run,
            team_lead=TeamLeadAgent().run,
        )
        self.executor = executor

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_head_agent_graph(
            state,
            workers=self.workers,
            executor=self.executor,
        )
