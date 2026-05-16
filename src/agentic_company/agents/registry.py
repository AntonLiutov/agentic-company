"""Registry of first-class delivery agents."""

from __future__ import annotations

from agentic_company.platform.agent_contracts import AgentDescriptor

AGENT_NODE_ROUTES: dict[str, tuple[str, str]] = {
    "business_analyst": ("business-analyst-agent", "request_business_analysis"),
    "architecture": ("architect-agent", "request_architecture"),
    "project_management": ("project-manager-agent", "request_project_management"),
    "team_lead": ("team-lead-agent", "request_sprint_delivery"),
    "fullstack": ("fullstack-agent", "delegate_feature"),
    "qa": ("qa-agent", "request_qa"),
    "deployment": ("deployment-agent", "request_deployment"),
    "handoff": ("documentation-handoff-agent", "request_handoff"),
}


def active_agents() -> list[AgentDescriptor]:
    """Return the active agent wrappers used by the company delivery graph."""

    from agentic_company.agents.architecture.agent import ArchitectAgent
    from agentic_company.agents.business_analysis.agent import BusinessAnalystAgent
    from agentic_company.agents.deployment.agent import AzureDeploymentAgent
    from agentic_company.agents.fullstack.agent import FullstackAgent
    from agentic_company.agents.handoff.agent import HandoffAgent
    from agentic_company.agents.head.agent import HeadAgent
    from agentic_company.agents.project_manager.agent import ProjectManagerAgent
    from agentic_company.agents.quality.agent import QualityAgent
    from agentic_company.agents.team_lead.agent import TeamLeadAgent

    return [
        HeadAgent.descriptor,
        BusinessAnalystAgent.descriptor,
        ArchitectAgent.descriptor,
        ProjectManagerAgent.descriptor,
        TeamLeadAgent.descriptor,
        FullstackAgent.descriptor,
        QualityAgent.descriptor,
        AzureDeploymentAgent.descriptor,
        HandoffAgent.descriptor,
    ]


def agent_by_id(agent_id: str) -> AgentDescriptor:
    """Find an active agent descriptor by id."""

    for descriptor in active_agents():
        if descriptor.agent_id == agent_id:
            return descriptor
    raise KeyError(f"Unknown active agent: {agent_id}")


def route_for_node(node_name: str) -> tuple[str, str]:
    """Return the default recipient and intent for a delivery node."""

    return AGENT_NODE_ROUTES.get(node_name, (node_name, f"request_{node_name}"))
