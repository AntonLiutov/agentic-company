"""First-class handoff agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.base import AgentDescriptor
from agentic_company.agents.handoff.codex_cli import HandoffCodexRunner
from agentic_company.agents.handoff.graph import run_handoff_agent_graph
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run handoff."""


class HandoffAgent:
    """Run the Handoff specialist as a Codex-owned delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="documentation-handoff-agent",
        name="Documentation / Handoff Agent",
        runtime="L6 Codex Handoff Agent",
        stage="handoff",
    )

    def __init__(self, runner: RunnerLike | None = None) -> None:
        self.runner = runner or HandoffCodexRunner()

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_handoff_agent_graph(state, runner=self.runner)
