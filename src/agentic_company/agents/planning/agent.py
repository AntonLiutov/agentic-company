"""First-class planning agent wrapper."""

from __future__ import annotations

from agentic_company.agents.base import AgentDescriptor
from agentic_company.agents.planning.graph import run_planning_agent_graph
from agentic_company.agents.planning.run import run_pipeline
from agentic_company.platform.state import DeliveryState


class PlanningAgent:
    """Run the deterministic planning pipeline as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="planning-agent",
        name="Planning Agent",
        runtime="L0 Deterministic",
        stage="planning",
    )

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_planning_agent_graph(state, run_pipeline)
