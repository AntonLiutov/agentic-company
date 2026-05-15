"""Internal LangGraph for the Deployment Agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.agents.deployment.codex_cli import (
    DeploymentCodexRunner,
    public_urls_from_deployment_result,
)
from agentic_company.platform.events import write_event
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

DEPLOYMENT_AGENT_ID = "deployment-agent"

DEPLOYMENT_AGENT_GRAPH_NODE_ORDER: tuple[str, ...] = (
    "prepare_context",
    "codex_deployment_execution",
    "parse_deployment_contract",
    "normalize_deployment_contract",
    "apply_deployment_result",
)


class DeploymentRunner(Protocol):
    """Codex-owned deployment execution boundary."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run deployment and return the parsed deployment result."""


class DeploymentAgentGraphState(TypedDict):
    """Internal state for the Deployment Agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    result: NotRequired[AgentRunResult]
    status: NotRequired[str]
    public_urls: NotRequired[list[str]]


def build_deployment_agent_graph(
    runner: DeploymentRunner | None = None,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the Deployment Agent internal graph.

    The graph intentionally contains no concrete Azure command sequence, service
    names, ports, Dockerfile names, or topology branches. Those choices belong to
    the Codex Deployment specialist inside the `codex_deployment_execution` node.
    """

    order = list(DEPLOYMENT_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Deployment agent graph requires at least one node.")

    graph = StateGraph(DeploymentAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "codex_deployment_execution": _codex_deployment_execution(runner),
        "parse_deployment_contract": _parse_deployment_contract,
        "normalize_deployment_contract": _normalize_deployment_contract,
        "apply_deployment_result": _apply_deployment_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_deployment_agent_graph(
    delivery_state: DeliveryState,
    *,
    runner: DeploymentRunner | None = None,
) -> DeliveryState:
    """Run the Deployment Agent subgraph and return updated delivery state."""

    graph_state: DeploymentAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_deployment_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_deployment_agent_graph_mermaid() -> str:
    """Render the Deployment Agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_deployment_agent_graph(cast(DeploymentRunner, NoopRunner()))
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    delivery_state = state["delivery_state"]
    event_log = Path(state["run_dir"]) / "events.jsonl"
    write_event(
        event_log,
        delivery_state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "deployment_started",
        {"release_strategy": "release_batch"},
    )
    return state


def _codex_deployment_execution(runner: DeploymentRunner | None):
    def run(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
        result = (runner or DeploymentCodexRunner()).run(Path(state["run_dir"]))
        return {**state, "result": result}

    return run


def _parse_deployment_contract(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    result = state.get("result")
    if result is None:
        return state
    status = _normalize_deployment_status(result.status)
    public_urls = public_urls_from_deployment_result(Path(state["run_dir"]))
    return {**state, "status": status, "public_urls": public_urls}


def _normalize_deployment_contract(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    """Reconcile Deployment Agent status with structured target evidence.

    This is intentionally a graph node rather than hidden parser logic. It is the
    future swap point for an LLM/AgentExecutor contract-normalizer. Today it
    performs a conservative evidence-based normalization: deployed requires
    usable public URL evidence; blocked/failed/unknown remain explicit statuses.
    """

    status = state.get("status")
    public_urls = state.get("public_urls", [])
    if status == "deployed" and not public_urls:
        return {**state, "status": "unknown"}
    return state


def _apply_deployment_result(state: DeploymentAgentGraphState) -> DeploymentAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("Deployment agent graph result is missing.")

    delivery_state = state["delivery_state"]
    deployment_status = state.get("status") or _normalize_deployment_status(result.status)
    public_urls = state.get("public_urls", [])
    event_log = Path(state["run_dir"]) / "events.jsonl"
    write_event(
        event_log,
        delivery_state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "artifact_written",
        {"artifact": "13-deployment-summary.md", "status": deployment_status},
    )
    write_event(
        event_log,
        delivery_state["run_id"],
        DEPLOYMENT_AGENT_ID,
        "deployment_completed",
        {"artifact": "13-deployment-summary.md", "status": deployment_status},
    )

    updated = mark_node_completed(
        delivery_state,
        node_name="deployment",
        stage="deployment",
        status=f"deployment_{deployment_status}",
    )
    updated["deployment_status"] = deployment_status
    updated["public_url"] = public_urls[0] if public_urls else None
    if public_urls:
        updated["public_urls"] = public_urls
    extend_artifacts(
        updated,
        artifact_refs(result.output_artifacts, kind="deployment", owner_agent=result.agent_id),
    )
    return {**state, "delivery_state": updated}


def _normalize_deployment_status(status: str) -> str:
    normalized = status.removeprefix("deployment_").removeprefix("codex_")
    return normalized if normalized in {"deployed", "blocked", "failed", "unknown"} else "unknown"
