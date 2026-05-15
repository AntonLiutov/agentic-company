"""First-class quality agent wrapper for the QA specialist role."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.base import AgentDescriptor, artifact_refs, extend_artifacts
from agentic_company.agents.quality.graph import run_quality_agent_graph
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run QA checks."""


class QualityAgent:
    """Run the QA specialist as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="qa-agent",
        name="QA Agent",
        runtime="L2 Tool Executor",
        stage="qa",
    )

    def __init__(self, runner: RunnerLike | None = None) -> None:
        self.runner = runner

    def run(self, state: DeliveryState) -> DeliveryState:
        if self.runner is None:
            return run_quality_agent_graph(state)

        result = self.runner.run(Path(state["run_dir"]))
        updated = mark_node_completed(state, node_name="qa", stage="qa", status=result.status)
        updated["qa_status"] = result.status.removeprefix("qa_")
        extend_artifacts(
            updated,
            artifact_refs(result.output_artifacts, kind="qa", owner_agent=result.agent_id),
        )
        return updated
