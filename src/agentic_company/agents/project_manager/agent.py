"""First-class Project Manager agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.project_manager.graph import run_project_manager_agent_graph
from agentic_company.platform.agent_contracts import (
    CODEX_EXEC_TOOL,
    AgentCapabilities,
    AgentCommunicationPolicy,
    AgentDescriptor,
    BaseAgentExecutorDeliveryAgent,
    coordinator_response_policy,
)
from agentic_company.platform.agent_runtime import SpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState
from agentic_company.ports.registry import worker_runner_for_agent


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run a Project Manager backend."""


class ProjectManagerAgent(BaseAgentExecutorDeliveryAgent):
    """Turn BA and architecture artifacts into bounded sprint plans."""

    descriptor = AgentDescriptor(
        agent_id="project-manager-agent",
        name="Project Manager Agent",
        runtime="L4 LangGraph Agent Executor + L6 Codex Project Manager",
        stage="project_management",
        family="planning-delivery",
    )
    default_capabilities = AgentCapabilities(tools=(CODEX_EXEC_TOOL,), can_use_codex=True)
    default_communication_policy = coordinator_response_policy()

    def __init__(
        self,
        runner: RunnerLike | None = None,
        *,
        agent_executor: SpecialistAgentExecutor | None = None,
        capabilities: AgentCapabilities | None = None,
        communication_policy: AgentCommunicationPolicy | None = None,
    ) -> None:
        super().__init__(
            agent_executor=agent_executor,
            capabilities=capabilities,
            communication_policy=communication_policy,
        )
        self.runner = runner or worker_runner_for_agent(
            "project-manager-agent",
            stage="project_management",
        )

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_project_manager_agent_graph(state, self.runner, self.agent_executor)
