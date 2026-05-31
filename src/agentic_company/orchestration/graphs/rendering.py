"""Graph rendering helpers for documentation and visual inspection."""

from __future__ import annotations

from collections.abc import Sequence

from agentic_company.agents.architecture.graph import ARCHITECT_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.business_analysis.graph import (
    BUSINESS_ANALYST_AGENT_GRAPH_NODE_ORDER,
)
from agentic_company.agents.deployment.graph import DEPLOYMENT_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.fullstack.graph import FULLSTACK_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.handoff.graph import HANDOFF_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.head.graph import HEAD_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.project_manager.graph import PROJECT_MANAGER_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.quality.graph import QUALITY_AGENT_GRAPH_NODE_ORDER
from agentic_company.agents.team_lead.graph import TEAM_LEAD_AGENT_GRAPH_NODE_ORDER
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

    head_nodes = _subgraph_nodes(
        "head",
        "Head Agent",
        HEAD_AGENT_GRAPH_NODE_ORDER,
    )
    head_edges = _subgraph_edges("head", HEAD_AGENT_GRAPH_NODE_ORDER)
    business_analyst_nodes = _subgraph_nodes(
        "business_analyst",
        "Business Analyst Agent",
        BUSINESS_ANALYST_AGENT_GRAPH_NODE_ORDER,
    )
    business_analyst_edges = _subgraph_edges(
        "business_analyst",
        BUSINESS_ANALYST_AGENT_GRAPH_NODE_ORDER,
    )
    architecture_nodes = _subgraph_nodes(
        "architecture",
        "Architect Agent",
        ARCHITECT_AGENT_GRAPH_NODE_ORDER,
    )
    architecture_edges = _subgraph_edges(
        "architecture",
        ARCHITECT_AGENT_GRAPH_NODE_ORDER,
    )
    project_manager_nodes = _subgraph_nodes(
        "project_manager",
        "Project Manager Agent",
        PROJECT_MANAGER_AGENT_GRAPH_NODE_ORDER,
    )
    project_manager_edges = _subgraph_edges(
        "project_manager",
        PROJECT_MANAGER_AGENT_GRAPH_NODE_ORDER,
    )
    team_lead_nodes = _subgraph_nodes(
        "team_lead", "Team Lead Agent", TEAM_LEAD_AGENT_GRAPH_NODE_ORDER
    )
    team_lead_edges = _subgraph_edges("team_lead", TEAM_LEAD_AGENT_GRAPH_NODE_ORDER)
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

\tsubgraph head_agent[Head Agent]
{head_nodes}
{head_edges}
\tend

\tsubgraph business_analyst_agent[Business Analyst Agent]
{business_analyst_nodes}
{business_analyst_edges}
\tend

\tsubgraph architecture_agent[Architect Agent]
{architecture_nodes}
{architecture_edges}
\tend

\tsubgraph project_manager_agent[Project Manager Agent]
{project_manager_nodes}
{project_manager_edges}
\tend

\tsubgraph team_lead_agent[Team Lead Agent]
{team_lead_nodes}
{team_lead_edges}
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

\t__start__ --> head_agent_entry;
\thead_run_agent_executor --> business_analyst_agent_entry;
\tbusiness_analyst_apply_result --> head_run_agent_executor;
\thead_run_agent_executor --> architecture_agent_entry;
\tarchitecture_apply_result --> head_run_agent_executor;
\thead_run_agent_executor --> project_manager_agent_entry;
\tproject_manager_apply_result --> head_run_agent_executor;
\thead_run_agent_executor --> team_lead_agent_entry;
\tteam_lead_apply_result --> head_run_agent_executor;
\thead_apply_result --> __end__;
\tteam_lead_run_agent_executor --> fullstack_agent_entry;
\tfullstack_apply_result --> team_lead_run_agent_executor;
\tteam_lead_run_agent_executor --> quality_agent_entry;
\tquality_apply_result --> team_lead_run_agent_executor;
\tteam_lead_run_agent_executor --> deployment_agent_entry;
\tdeployment_apply_result --> team_lead_run_agent_executor;
\tteam_lead_run_agent_executor --> handoff_agent_entry;
\thandoff_apply_result --> team_lead_run_agent_executor;
\tclassDef default fill:#f2f0ff,line-height:1.2
\tclassDef first fill-opacity:0
\tclassDef last fill:#bfb6fc
"""


def render_company_agent_map_mermaid() -> str:
    """Render the current company-agent topology for architecture discussion."""

    return """---
config:
  flowchart:
    curve: linear
---
graph TD;
    platform["Platform Graph Runner"]
    state[("delivery/run-state.json<br/>structured trace")]
    head["Head Agent<br/>company coordinator"]
    ba["Business Analyst<br/>Codex worker"]
    architect["Architect<br/>Codex worker"]
    pm["Project Manager<br/>Codex worker"]
    review["Codex Review<br/>read-only"]

    tl["Team Lead<br/>delivery coordinator"]
    fs["Fullstack<br/>Codex worker"]
    qa["QA<br/>Codex worker"]
    deploy["Deployment<br/>Codex worker"]
    handoff["Handoff<br/>Codex worker"]

    platform -->|"active node"| head
    platform -.->|"persists"| state
    head -->|"request_business_analysis"| ba
    ba -->|"agent_response"| head
    head -->|"request_architecture"| architect
    architect -->|"agent_response"| head
    head -->|"request_project_management"| pm
    pm -->|"agent_response"| head
    head -.->|"internal quality review"| review
    review -.->|"quality report / feedback"| head
    head -->|"request_sprint_delivery"| tl
    head -->|"persists planning state"| state
    tl -->|"delegate_feature"| fs
    fs -->|"agent_response"| tl
    tl -->|"request_qa"| qa
    qa -->|"agent_response"| tl
    tl -->|"request_deployment"| deploy
    deploy -->|"agent_response"| tl
    tl -->|"request_handoff"| handoff
    handoff -->|"agent_response"| tl
    tl -->|"sprint result"| state

    classDef active fill:#e8f7ee,stroke:#2e7d32,color:#15351f
    classDef paused fill:#fff6df,stroke:#b7791f,color:#3d2b00,stroke-dasharray:5 3
    classDef platform fill:#eef2ff,stroke:#4f46e5,color:#1e1b4b
    classDef artifact fill:#f7f7f8,stroke:#6b7280,color:#111827
    class platform,state platform
    class head,ba,architect,pm,review,tl,fs,qa,deploy,handoff active
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
