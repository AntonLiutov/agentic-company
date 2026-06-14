"""Worker provider registry."""

from __future__ import annotations

from collections.abc import Callable

from agentic_company.integrations.codex.worker import (
    CodexWorkerAdapter,
    LegacyCodexRunner,
    WorkerBackedAgentRunner,
)
from agentic_company.ports.worker import WorkerPort

WorkerFactory = Callable[[str], WorkerPort]

_WORKER_FACTORIES: dict[str, WorkerFactory] = {}


def register_worker_provider(provider: str, factory: WorkerFactory) -> None:
    """Register a worker provider factory."""

    normalized = _normalize_provider(provider)
    _WORKER_FACTORIES[normalized] = factory


def worker_for_agent(agent_id: str, *, provider: str = "codex") -> WorkerPort:
    """Return a worker adapter for one agent/provider pair."""

    normalized = _normalize_provider(provider)
    if normalized == "codex" and normalized not in _WORKER_FACTORIES:
        register_worker_provider("codex", _codex_worker_for_agent)
    try:
        factory = _WORKER_FACTORIES[normalized]
    except KeyError as exc:
        raise ValueError(f"No worker provider is registered for {provider!r}.") from exc
    return factory(agent_id)


def worker_runner_for_agent(
    agent_id: str,
    *,
    provider: str = "codex",
    stage: str = "",
) -> WorkerBackedAgentRunner:
    """Return the existing AgentRunResult runner facade backed by WorkerPort."""

    return WorkerBackedAgentRunner(
        worker_for_agent(agent_id, provider=provider),
        agent_id=agent_id,
        stage=stage,
    )


def _normalize_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if not normalized:
        raise ValueError("Worker provider is required.")
    return normalized


def _codex_worker_for_agent(agent_id: str) -> WorkerPort:
    return CodexWorkerAdapter(_codex_runner_for_agent(agent_id))


def _codex_runner_for_agent(agent_id: str) -> LegacyCodexRunner:
    if agent_id == "business-analyst-agent":
        from agentic_company.agents.business_analysis.codex_cli import (
            BusinessAnalystCodexRunner,
        )

        return BusinessAnalystCodexRunner()
    if agent_id == "architect-agent":
        from agentic_company.agents.architecture.codex_cli import ArchitectCodexRunner

        return ArchitectCodexRunner()
    if agent_id == "project-manager-agent":
        from agentic_company.agents.project_manager.codex_cli import ProjectManagerCodexRunner

        return ProjectManagerCodexRunner()
    if agent_id == "fullstack-agent":
        from agentic_company.agents.fullstack.codex_cli import CodexCliRunner

        return CodexCliRunner()
    if agent_id == "qa-agent":
        from agentic_company.agents.quality.codex_cli import QualityCodexRunner

        return QualityCodexRunner()
    if agent_id == "deployment-agent":
        from agentic_company.agents.deployment.codex_cli import DeploymentCodexRunner

        return DeploymentCodexRunner()
    if agent_id == "documentation-handoff-agent":
        from agentic_company.agents.handoff.codex_cli import HandoffCodexRunner

        return HandoffCodexRunner()
    raise ValueError(f"No Codex worker runner is registered for agent {agent_id!r}.")
