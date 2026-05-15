"""First-class deployment agent wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.base import (
    AgentDescriptor,
    artifact_refs,
    extend_artifacts,
)
from agentic_company.agents.deployment.graph import run_deployment_agent_graph
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run deployment."""


class AzureDeploymentAgent:
    """Deploy the generated project with the current Azure deployment runner."""

    descriptor = AgentDescriptor(
        agent_id="deployment-agent",
        name="Deployment Agent",
        runtime="L2 Tool Executor",
        stage="deployment",
    )

    def __init__(self, runner: RunnerLike | None = None) -> None:
        self._use_graph = runner is None
        if runner is None:
            from agentic_company.agents.deployment.runner import AzureDeploymentRunner

            runner = AzureDeploymentRunner()
        self.runner = runner

    def run(self, state: DeliveryState) -> DeliveryState:
        if not self._use_graph:
            result = self.runner.run(Path(state["run_dir"]))
            deployment_status = result.status.removeprefix("deployment_")
            updated = mark_node_completed(
                state,
                node_name="deployment",
                stage="deployment",
                status=result.status,
            )
            updated["deployment_status"] = deployment_status
            extend_artifacts(
                updated,
                artifact_refs(
                    result.output_artifacts,
                    kind="deployment",
                    owner_agent=result.agent_id,
                ),
            )
            return updated

        return run_deployment_agent_graph(state, self.runner)
