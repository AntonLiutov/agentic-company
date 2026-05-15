"""Internal LangGraph for the fullstack agent."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import artifact_refs, extend_artifacts
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, mark_node_completed

FULLSTACK_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "run_codex",
    "apply_result",
]


class FullstackRunnerLike(Protocol):
    """Runner contract used by the fullstack graph."""

    def run(self, run_dir: Path) -> AgentRunResult:
        """Run an implementation backend."""


class FullstackAgentGraphState(TypedDict):
    """Internal state for the fullstack agent subgraph."""

    run_dir: str
    delivery_state: DeliveryState
    result: NotRequired[AgentRunResult]


def build_fullstack_agent_graph(
    runner: FullstackRunnerLike,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the fullstack agent internal graph."""

    order = list(FULLSTACK_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Fullstack agent graph requires at least one node.")

    graph = StateGraph(FullstackAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "run_codex": _run_codex(runner),
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_fullstack_agent_graph(
    delivery_state: DeliveryState,
    runner: FullstackRunnerLike,
) -> DeliveryState:
    """Run the fullstack agent subgraph and return updated delivery state."""

    graph_state: FullstackAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
    }
    result = build_fullstack_agent_graph(runner).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_fullstack_agent_graph_mermaid() -> str:
    """Render the fullstack agent subgraph as Mermaid text."""

    class NoopRunner:
        def run(self, run_dir: Path) -> AgentRunResult:
            raise RuntimeError("Runner is not available in graph rendering.")

    return (
        build_fullstack_agent_graph(cast(FullstackRunnerLike, NoopRunner()))
        .get_graph()
        .draw_mermaid()
    )


def _prepare_context(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    return state


def _run_codex(runner: FullstackRunnerLike):
    def run(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
        result = runner.run(Path(state["run_dir"]))
        return {**state, "result": result}

    return run


def _apply_result(state: FullstackAgentGraphState) -> FullstackAgentGraphState:
    result = state.get("result")
    if result is None:
        raise ValueError("Fullstack agent graph result is missing.")

    delivery_state = state["delivery_state"]
    updated = mark_node_completed(
        delivery_state,
        node_name="fullstack",
        stage="fullstack",
        status=result.status,
    )
    extend_artifacts(
        updated,
        artifact_refs(
            result.output_artifacts,
            kind="execution",
            owner_agent=result.agent_id,
        ),
    )
    return {**state, "delivery_state": updated}
