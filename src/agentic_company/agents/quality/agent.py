"""First-class quality agent wrapper for the QA specialist role."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.base import AgentDescriptor
from agentic_company.agents.quality.feature_qa import run_feature_quality_agent
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run QA checks."""


class QualityAgent:
    """Run the QA specialist as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="qa-agent",
        name="QA Agent",
        runtime="L6 Codex QA Agent",
        stage="qa",
    )

    def __init__(self, runner: RunnerLike | None = None) -> None:
        self.runner = runner

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_feature_quality_agent(state, runner=self.runner)
