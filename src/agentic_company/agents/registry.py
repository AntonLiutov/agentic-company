"""Registry of first-class delivery agents."""

from __future__ import annotations

from agentic_company.agents.base import AgentDescriptor


def active_agents() -> list[AgentDescriptor]:
    """Return the active agent wrappers used by the company delivery graph."""

    from agentic_company.agents.deployment.agent import AzureDeploymentAgent
    from agentic_company.agents.fullstack.agent import FullstackAgent
    from agentic_company.agents.handoff.agent import HandoffAgent
    from agentic_company.agents.planning.agent import PlanningAgent
    from agentic_company.agents.quality.agent import QualityAgent

    return [
        PlanningAgent.descriptor,
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
