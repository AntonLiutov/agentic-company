"""First-class Architect agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.architecture.graph import run_architect_agent_graph
from agentic_company.platform.agent.agent_contracts import (
    CODEX_EXEC_TOOL,
    AgentCapabilities,
    AgentCommunicationPolicy,
    AgentDescriptor,
    BaseAgentExecutorDeliveryAgent,
    coordinator_response_policy,
)
from agentic_company.platform.agent.agent_runtime import SpecialistAgentExecutor
from agentic_company.platform.db.models import AgentRunResult
from agentic_company.platform.db.state import DeliveryState
from agentic_company.ports.registry import worker_runner_for_agent


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run an Architect backend."""


class ArchitectAgent(BaseAgentExecutorDeliveryAgent):
    """Translate BA output into technical architecture artifacts."""

    descriptor = AgentDescriptor(
        agent_id="architect-agent",
        name="Architect Agent",
        runtime="L4 LangGraph Agent Executor + L6 Codex Architect",
        stage="architecture",
        family="technical-planning",
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
            "architect-agent",
            stage="architecture",
        )

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_architect_agent_graph(state, self.runner, self.agent_executor)
