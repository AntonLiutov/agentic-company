"""Company delivery LangGraph shell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.orchestration.graphs.nodes import DeliveryGraphNodes
from agentic_company.orchestration.graphs.routing import (
    CONSOLE_EXECUTION_NODE_ORDER,
    DELIVERY_GRAPH_NODE_ORDER,
)
from agentic_company.platform.state import DeliveryState

CompiledDeliveryGraph = Any


def build_delivery_graph(
    nodes: DeliveryGraphNodes | None = None,
    *,
    node_order: Sequence[str] | None = None,
) -> CompiledDeliveryGraph:
    """Build the first linear company delivery graph.

    Stage 1 intentionally keeps routing linear. Later stages can replace the direct edges with
    conditional routing without changing the public graph entry point.
    """

    order = list(node_order or DELIVERY_GRAPH_NODE_ORDER)
    if not order:
        raise ValueError("Delivery graph requires at least one node.")

    graph_nodes = nodes or DeliveryGraphNodes()
    graph = StateGraph(DeliveryState)
    node_map: dict[str, Callable[[DeliveryState], DeliveryState]] = {
        "planning": graph_nodes.planning,
        "fullstack": graph_nodes.fullstack,
        "qa": graph_nodes.qa,
        "deployment": graph_nodes.deployment,
        "handoff": graph_nodes.handoff,
    }

    for name in order:
        graph.add_node(name, node_map[name])

    if order == CONSOLE_EXECUTION_NODE_ORDER:
        graph.add_edge(START, "fullstack")
        graph.add_conditional_edges(
            "fullstack",
            _route_after_fullstack,
            {
                "qa": "qa",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "qa",
            _route_after_qa,
            {
                "fullstack": "fullstack",
                "deployment": "deployment",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "deployment",
            _route_after_deployment,
            {
                "handoff": "handoff",
                "end": END,
            },
        )
        graph.add_edge("handoff", END)
        return graph.compile()

    graph.add_edge(START, order[0])
    for current, next_node in zip(
        order,
        order[1:],
        strict=False,
    ):
        graph.add_edge(current, next_node)
    graph.add_edge(order[-1], END)
    return graph.compile()


def run_delivery_graph(
    state: DeliveryState,
    *,
    nodes: DeliveryGraphNodes | None = None,
    node_order: Sequence[str] | None = None,
) -> DeliveryState:
    """Run the linear delivery graph and return the final state."""

    result = build_delivery_graph(nodes, node_order=node_order).invoke(state)
    return cast(DeliveryState, result)


def _route_after_fullstack(state: DeliveryState) -> str:
    if state.get("blockers"):
        return "end"
    if state.get("project_archetype") == "api-web-compose":
        if state.get("status") in {"fullstack_feature_implemented", "already_completed"}:
            return "qa"
        return "end"
    return "qa"


def _route_after_qa(state: DeliveryState) -> str:
    if state.get("blockers"):
        return "end"
    if state.get("project_archetype") == "api-web-compose":
        if state.get("active_feature_id"):
            return "fullstack"
        if state.get("qa_status") == "passed":
            return "deployment"
        return "end"
    if state.get("qa_status") == "passed":
        return "deployment"
    return "end"


def _route_after_deployment(state: DeliveryState) -> str:
    if state.get("blockers"):
        return "end"
    if state.get("deployment_status") == "deployed":
        return "handoff"
    if state.get("status") == "deployment_deployed":
        return "handoff"
    return "end"
