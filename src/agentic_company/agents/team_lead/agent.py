"""First-class Team Lead agent wrapper."""

from __future__ import annotations

from agentic_company.agents.deployment.agent import AzureDeploymentAgent
from agentic_company.agents.fullstack.agent import FullstackAgent
from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.agents.team_lead.contracts import TEAM_LEAD_TOOLS
from agentic_company.agents.team_lead.graph import TeamLeadExecutor, run_team_lead_agent_graph
from agentic_company.agents.team_lead.tools import TeamLeadWorkers
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

TEAM_LEAD_ALLOWED_RECIPIENTS: tuple[str, ...] = (
    "head-agent",
    "project-manager-agent",
    "fullstack-agent",
    "qa-agent",
    "deployment-agent",
    "documentation-handoff-agent",
)

TEAM_LEAD_ALLOWED_MESSAGE_INTENTS: tuple[str, ...] = (
    "delegate_feature",
    "request_qa",
    "request_deployment",
    "request_handoff",
    "report_status",
    "request_clarification",
    "escalate_blocker",
)

TEAM_LEAD_ALLOWED_MESSAGE_ROUTES: tuple[tuple[str, str], ...] = (
    ("fullstack-agent", "delegate_feature"),
    ("fullstack-agent", "report_status"),
    ("fullstack-agent", "request_clarification"),
    ("fullstack-agent", "escalate_blocker"),
    ("qa-agent", "request_qa"),
    ("qa-agent", "report_status"),
    ("qa-agent", "request_clarification"),
    ("qa-agent", "escalate_blocker"),
    ("deployment-agent", "request_deployment"),
    ("deployment-agent", "report_status"),
    ("deployment-agent", "request_clarification"),
    ("deployment-agent", "escalate_blocker"),
    ("documentation-handoff-agent", "request_handoff"),
    ("documentation-handoff-agent", "report_status"),
    ("documentation-handoff-agent", "request_clarification"),
    ("documentation-handoff-agent", "escalate_blocker"),
    ("project-manager-agent", "report_status"),
    ("project-manager-agent", "request_clarification"),
    ("project-manager-agent", "escalate_blocker"),
    ("head-agent", "report_status"),
    ("head-agent", "request_clarification"),
    ("head-agent", "escalate_blocker"),
)


class TeamLeadAgent(BaseDeliveryAgent):
    """Coordinate sprint execution across specialist delivery agents."""

    descriptor = AgentDescriptor(
        agent_id="team-lead-agent",
        name="Team Lead Agent",
        runtime="L4 LangGraph Agent Executor",
        stage="team_lead",
        family="delivery-coordination",
    )
    default_capabilities = AgentCapabilities(
        tools=unique_tools(
            COMMON_AGENT_TOOLS,
            COORDINATOR_AGENT_TOOLS,
            CODEX_REVIEW_TOOLS,
            TEAM_LEAD_TOOLS,
        ),
        can_delegate=True,
        can_request_human=True,
    )
    default_communication_policy = AgentCommunicationPolicy(
        allowed_recipients=TEAM_LEAD_ALLOWED_RECIPIENTS,
        allowed_intents=TEAM_LEAD_ALLOWED_MESSAGE_INTENTS,
        allowed_routes=TEAM_LEAD_ALLOWED_MESSAGE_ROUTES,
    )

    def __init__(
        self,
        workers: TeamLeadWorkers | None = None,
        executor: TeamLeadExecutor | None = None,
        *,
        capabilities: AgentCapabilities | None = None,
        communication_policy: AgentCommunicationPolicy | None = None,
    ) -> None:
        super().__init__(
            capabilities=capabilities,
            communication_policy=communication_policy,
        )
        self.workers = workers or TeamLeadWorkers(
            fullstack=FullstackAgent().run,
            qa=QualityAgent().run,
            deployment=AzureDeploymentAgent().run,
            handoff=HandoffAgent().run,
        )
        self.executor = executor

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_team_lead_agent_graph(
            state,
            workers=self.workers,
            executor=self.executor,
        )
