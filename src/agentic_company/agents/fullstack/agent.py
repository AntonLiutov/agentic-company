"""First-class fullstack agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.fullstack.codex_cli import CodexCliRunner
from agentic_company.agents.fullstack.graph import run_fullstack_agent_graph
from agentic_company.platform.agent_contracts import (
    AgentCapabilities,
    AgentCommunicationPolicy,
    AgentDescriptor,
    BaseAgentExecutorDeliveryAgent,
    codex_delivery_capabilities,
    coordinator_response_policy,
)
from agentic_company.platform.agent_runtime import SpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState


class RunnerLike(Protocol):
    def run(self, run_dir: Path) -> AgentRunResult:
        """Run an implementation backend."""


class FullstackAgent(BaseAgentExecutorDeliveryAgent):
    """Run the implementation backend as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="fullstack-agent",
        name="Fullstack Agent",
        runtime="L4 LangGraph Agent Executor + L6 Codex Agent",
        stage="fullstack",
    )
    default_capabilities = codex_delivery_capabilities()
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
        self.runner = runner or CodexCliRunner()

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_fullstack_agent_graph(state, self.runner, self.agent_executor)
