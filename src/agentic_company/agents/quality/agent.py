"""First-class quality agent wrapper for the QA specialist role."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.quality.graph import run_quality_agent_graph
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
        """Run QA checks."""


class QualityAgent(BaseAgentExecutorDeliveryAgent):
    """Run the QA specialist as a delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="qa-agent",
        name="QA Agent",
        runtime="L4 LangGraph Agent Executor + L6 Codex QA Agent",
        stage="qa",
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
        self.runner = runner

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_quality_agent_graph(
            state,
            runner=self.runner,
            agent_executor=self.agent_executor,
        )
