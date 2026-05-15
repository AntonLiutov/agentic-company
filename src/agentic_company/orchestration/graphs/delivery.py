"""Company delivery LangGraph shell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from agentic_company.orchestration.graphs.nodes import DeliveryGraphNodes
from agentic_company.orchestration.graphs.routing import DELIVERY_GRAPH_NODE_ORDER
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
