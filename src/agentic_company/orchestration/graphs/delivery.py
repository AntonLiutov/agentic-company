"""Platform LangGraph shell for running configured company nodes."""

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
    """Build a linear platform graph from configured node names.

    The graph runner owns execution mechanics. Agentic coordination decisions
    live inside coordinator nodes such as Head Agent or Team Lead.
    """

    order = list(node_order or DELIVERY_GRAPH_NODE_ORDER)
    if not order:
        raise ValueError("Delivery graph requires at least one node.")

    graph_nodes = nodes or DeliveryGraphNodes()
    graph = StateGraph(DeliveryState)
    node_map: dict[str, Callable[[DeliveryState], DeliveryState]] = {
        "head": graph_nodes.head,
        "business_analyst": graph_nodes.business_analyst,
        "architecture": graph_nodes.architecture,
        "project_management": graph_nodes.project_management,
        "team_lead": graph_nodes.team_lead,
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
    """Run the configured platform graph and return the final state."""

    result = build_delivery_graph(nodes, node_order=node_order).invoke(state)
    return cast(DeliveryState, result)
