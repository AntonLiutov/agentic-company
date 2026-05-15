"""Internal LangGraph for the handoff agent."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.agents.base import extend_artifacts, target_project_dir
from agentic_company.platform.artifacts import artifact_ref
from agentic_company.platform.state import DeliveryState, mark_node_completed

HANDOFF_AGENT_GRAPH_NODE_ORDER = [
    "prepare_context",
    "write_handoff_summary",
    "apply_result",
]

HandoffWriter = Callable[[Path, Path, str], str]


class HandoffAgentGraphState(TypedDict):
    """Internal state for the handoff agent subgraph."""

    delivery_state: DeliveryState
    run_dir: str
    target_project_dir: str
    artifact: NotRequired[str]


def build_handoff_agent_graph(
    writer: HandoffWriter,
    *,
    node_order: Sequence[str] | None = None,
):
    """Build the handoff agent internal graph."""

    order = list(HANDOFF_AGENT_GRAPH_NODE_ORDER if node_order is None else node_order)
    if not order:
        raise ValueError("Handoff agent graph requires at least one node.")

    graph = StateGraph(HandoffAgentGraphState)
    node_map = {
        "prepare_context": _prepare_context,
        "write_handoff_summary": _write_handoff_summary(writer),
        "apply_result": _apply_result,
    }
    for name in order:
        graph.add_node(name, node_map[name])

    graph.add_edge(START, order[0])
    for current, next_node in zip(order, order[1:], strict=False):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_handoff_agent_graph(
    delivery_state: DeliveryState,
    writer: HandoffWriter,
) -> DeliveryState:
    """Run the handoff agent subgraph and return updated delivery state."""

    graph_state: HandoffAgentGraphState = {
        "delivery_state": delivery_state,
        "run_dir": delivery_state["run_dir"],
        "target_project_dir": str(target_project_dir(delivery_state)),
    }
    result = build_handoff_agent_graph(writer).invoke(graph_state)
    return cast(DeliveryState, result["delivery_state"])


def render_handoff_agent_graph_mermaid() -> str:
    """Render the handoff agent subgraph as Mermaid text."""

    def noop_writer(run_dir: Path, target_dir: Path, run_id: str) -> str:
        raise RuntimeError("Writer is not available in graph rendering.")

    return build_handoff_agent_graph(noop_writer).get_graph().draw_mermaid()


def _prepare_context(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    return state


def _write_handoff_summary(writer: HandoffWriter):
    def write(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
        delivery_state = state["delivery_state"]
        artifact = writer(
            Path(state["run_dir"]),
            Path(state["target_project_dir"]),
            delivery_state["run_id"],
        )
        return {**state, "artifact": artifact}

    return write


def _apply_result(state: HandoffAgentGraphState) -> HandoffAgentGraphState:
    artifact = state.get("artifact")
    if artifact is None:
        raise ValueError("Handoff agent graph artifact is missing.")

    delivery_state = state["delivery_state"]
    updated = mark_node_completed(
        delivery_state,
        node_name="handoff",
        stage="handoff",
        status="completed",
    )
    extend_artifacts(
        updated,
        [
            artifact_ref(
                artifact,
                kind="handoff",
                owner_agent="documentation-handoff-agent",
            )
        ],
    )
    return {**state, "delivery_state": updated}
