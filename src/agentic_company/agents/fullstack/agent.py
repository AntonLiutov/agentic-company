"""First-class fullstack agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.base import AgentDescriptor
from agentic_company.agents.fullstack.codex_cli import CodexCliRunner
from agentic_company.agents.fullstack.graph import run_fullstack_agent_graph
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run an implementation backend."""


class FullstackAgent:
    """Run the implementation backend as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="fullstack-agent",
        name="Fullstack Agent",
        runtime="L6 Codex Agent",
        stage="fullstack",
    )

    def __init__(self, runner: RunnerLike | None = None) -> None:
        self.runner = runner or CodexCliRunner()

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_fullstack_agent_graph(state, self.runner)
