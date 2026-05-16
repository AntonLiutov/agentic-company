"""Platform graph node wrappers around first-class agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentic_company.agents.architecture.agent import ArchitectAgent
from agentic_company.agents.business_analysis.agent import BusinessAnalystAgent
from agentic_company.agents.deployment.agent import AzureDeploymentAgent
from agentic_company.agents.fullstack.agent import FullstackAgent
from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.head.agent import HeadAgent
from agentic_company.agents.project_manager.agent import ProjectManagerAgent
from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.agents.team_lead.agent import TeamLeadAgent
from agentic_company.platform.state import DeliveryState

DeliveryNode = Callable[[DeliveryState], DeliveryState]


@dataclass(slots=True)
class DeliveryGraphNodes:
    """Injectable node functions used to build the company delivery graph."""

    business_analyst: DeliveryNode | None = None
    architecture: DeliveryNode | None = None
    project_management: DeliveryNode | None = None
    head: DeliveryNode | None = None
    team_lead: DeliveryNode | None = None
    fullstack: DeliveryNode | None = None
    qa: DeliveryNode | None = None
    deployment: DeliveryNode | None = None
    handoff: DeliveryNode | None = None

    def __post_init__(self) -> None:
        if self.head is None:
            self.head = head_node
        if self.business_analyst is None:
            self.business_analyst = business_analyst_node
        if self.architecture is None:
            self.architecture = architecture_node
        if self.project_management is None:
            self.project_management = project_management_node
        if self.team_lead is None:
            self.team_lead = team_lead_node
        if self.fullstack is None:
            self.fullstack = fullstack_node
        if self.qa is None:
            self.qa = qa_node
        if self.deployment is None:
            self.deployment = deployment_node
        if self.handoff is None:
            self.handoff = handoff_node


def head_node(state: DeliveryState) -> DeliveryState:
    """Run the Head Agent."""

    return HeadAgent().run(state)


def business_analyst_node(state: DeliveryState) -> DeliveryState:
    """Run the Business Analyst agent."""

    return BusinessAnalystAgent().run(state)


def architecture_node(state: DeliveryState) -> DeliveryState:
    """Run the Architect agent."""

    return ArchitectAgent().run(state)


def project_management_node(state: DeliveryState) -> DeliveryState:
    """Run the Project Manager agent."""

    return ProjectManagerAgent().run(state)


def team_lead_node(state: DeliveryState) -> DeliveryState:
    """Run the Team Lead agent."""

    return TeamLeadAgent().run(state)


def fullstack_node(state: DeliveryState) -> DeliveryState:
    """Run the fullstack agent."""

    return FullstackAgent().run(state)


def qa_node(state: DeliveryState) -> DeliveryState:
    """Run the QA agent."""

    return QualityAgent().run(state)


def deployment_node(state: DeliveryState) -> DeliveryState:
    """Run the deployment agent."""

    return AzureDeploymentAgent().run(state)


def handoff_node(state: DeliveryState) -> DeliveryState:
    """Run the handoff agent."""

    return HandoffAgent().run(state)
