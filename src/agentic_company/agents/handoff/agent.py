"""First-class handoff agent wrapper."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentic_company.agents.base import AgentDescriptor
from agentic_company.agents.handoff.graph import run_handoff_agent_graph
from agentic_company.agents.handoff.summary import write_handoff_summary
from agentic_company.platform.state import DeliveryState

HandoffWriter = Callable[[Path, Path, str], str]


class HandoffAgent:
    """Write handoff artifacts as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="documentation-handoff-agent",
        name="Documentation / Handoff Agent",
        runtime="L0 Deterministic",
        stage="handoff",
    )

    def __init__(self, writer: HandoffWriter = write_handoff_summary) -> None:
        self.writer = writer

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_handoff_agent_graph(state, self.writer)
