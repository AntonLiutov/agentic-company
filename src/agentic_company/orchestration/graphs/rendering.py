"""Graph rendering helpers for documentation and visual inspection."""

from __future__ import annotations

from collections.abc import Sequence

from agentic_company.agents.deployment.graph import DEPLOYMENT_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.fullstack.graph import FULLSTACK_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.handoff.graph import HANDOFF_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.planning.graph import PLANNING_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.quality.graph import QUALITY_AGENT_GRAPH_NODE_ORDER
from agentic_company.orchestration.graphs.delivery import build_delivery_graph
from agentic_company.orchestration.graphs.nodes import DeliveryGraphNodes


def render_delivery_graph_mermaid(
    *,
    nodes: DeliveryGraphNodes | None = None,
    node_order: Sequence[str] | None = None,
) -> str:
    """Render the current LangGraph delivery design as Mermaid text."""

    return build_delivery_graph(nodes=nodes, node_order=node_order).get_graph().draw_mermaid()


def render_delivery_expanded_graph_mermaid() -> str:
    """Render a documentation graph with known agent subgraphs expanded inline."""

    planning_nodes = _subgraph_nodes("planning", "Planning Agent", PLANNING_AGENT_GRAPH_NODE_ORDER)
    planning_edges = _subgraph_edges("planning", PLANNING_AGENT_GRAPH_NODE_ORDER)
    fullstack_nodes = _subgraph_nodes(
        "fullstack", "Fullstack Agent", FULLSTACK_AGENT_GRAPH_NODE_ORDER
    )
    fullstack_edges = _subgraph_edges("fullstack", FULLSTACK_AGENT_GRAPH_NODE_ORDER)
    quality_nodes = _subgraph_nodes("quality", "QA Agent", QUALITY_AGENT_GRAPH_NODE_ORDER)
    quality_edges = _subgraph_edges("quality", QUALITY_AGENT_GRAPH_NODE_ORDER)
    deployment_nodes = _subgraph_nodes(
        "deployment", "Deployment Agent", DEPLOYMENT_AGENT_GRAPH_NODE_ORDER
    )
    deployment_edges = _subgraph_edges("deployment", DEPLOYMENT_AGENT_GRAPH_NODE_ORDER)
    handoff_nodes = _subgraph_nodes("handoff", "Handoff Agent", HANDOFF_AGENT_GRAPH_NODE_ORDER)
    handoff_edges = _subgraph_edges("handoff", HANDOFF_AGENT_GRAPH_NODE_ORDER)
    return f"""---
config:
  flowchart:
    curve: linear
---
graph TD;
\t__start__([<p>__start__</p>]):::first
\t__end__([<p>__end__</p>]):::last

\tsubgraph planning_agent[Planning Agent]
{planning_nodes}
{planning_edges}
\tend

\tsubgraph fullstack_agent[Fullstack Agent]
{fullstack_nodes}
{fullstack_edges}
\tend

\tsubgraph quality_agent[QA Agent]
{quality_nodes}
{quality_edges}
\tend

\tsubgraph deployment_agent[Deployment Agent]
{deployment_nodes}
{deployment_edges}
\tend

\tsubgraph handoff_agent[Handoff Agent]
{handoff_nodes}
{handoff_edges}
\tend

\t__start__ --> planning_agent_entry;
\tplanning_apply_result --> fullstack_agent_entry;
\tfullstack_apply_result --> quality_agent_entry;
\tquality_apply_result --> deployment_agent_entry;
\tdeployment_apply_result --> handoff_agent_entry;
\thandoff_apply_result --> __end__;
\tclassDef default fill:#f2f0ff,line-height:1.2
\tclassDef first fill-opacity:0
\tclassDef last fill:#bfb6fc
"""


def _subgraph_nodes(prefix: str, entry_label: str, node_order: Sequence[str]) -> str:
    nodes = [f'\t\t{prefix}_agent_entry["{entry_label}"]']
    nodes.extend(f'\t\t{prefix}_{name}["{_node_label(name)}"]' for name in node_order)
    return "\n".join(nodes)


def _subgraph_edges(prefix: str, node_order: Sequence[str]) -> str:
    edges = [f"\t\t{prefix}_agent_entry --> {prefix}_{node_order[0]};"] if node_order else []
    edges.extend(
        f"\t\t{prefix}_{current} --> {prefix}_{next_node};"
        for current, next_node in zip(node_order, node_order[1:], strict=False)
    )
    return "\n".join(edges)


def _node_label(name: str) -> str:
    label = name.replace("_", " ").title()
    return label.replace(" Qa", " QA").replace("Qa ", "QA ").replace("Post Deploy", "Post-Deploy")
