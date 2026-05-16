"""First-class handoff agent wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_company.agents.handoff.codex_cli import HandoffCodexRunner
from agentic_company.agents.handoff.graph import run_handoff_agent_graph
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
        """Run handoff."""


class HandoffAgent(BaseAgentExecutorDeliveryAgent):
    """Run the Handoff specialist as a Codex-owned delivery graph agent."""

    descriptor = AgentDescriptor(
        agent_id="documentation-handoff-agent",
        name="Documentation / Handoff Agent",
        runtime="L4 LangGraph Agent Executor + L6 Codex Handoff Agent",
        stage="handoff",
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
        self.runner = runner or HandoffCodexRunner()

    def run(self, state: DeliveryState) -> DeliveryState:
        return run_handoff_agent_graph(
            state,
            runner=self.runner,
            agent_executor=self.agent_executor,
        )
