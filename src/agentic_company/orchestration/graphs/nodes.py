"""Delivery graph node wrappers around first-class delivery agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentic_company.agents.deployment.agent import AzureDeploymentAgent
from agentic_company.agents.fullstack.agent import FullstackAgent
from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.planning.agent import PlanningAgent
from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.platform.state import DeliveryState

DeliveryNode = Callable[[DeliveryState], DeliveryState]


@dataclass(slots=True)
class DeliveryGraphNodes:
    """Injectable node functions used to build the company delivery graph."""

    planning: DeliveryNode | None = None
    fullstack: DeliveryNode | None = None
    qa: DeliveryNode | None = None
    deployment: DeliveryNode | None = None
    handoff: DeliveryNode | None = None

    def __post_init__(self) -> None:
        if self.planning is None:
            self.planning = planning_node
        if self.fullstack is None:
            self.fullstack = fullstack_node
        if self.qa is None:
            self.qa = qa_node
        if self.deployment is None:
            self.deployment = deployment_node
        if self.handoff is None:
            self.handoff = handoff_node


def planning_node(state: DeliveryState) -> DeliveryState:
    """Run the planning agent."""

    return PlanningAgent().run(state)


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
