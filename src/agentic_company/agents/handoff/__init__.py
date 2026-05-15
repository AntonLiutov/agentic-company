"""Documentation / Handoff Agent implementation package."""

from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.handoff.graph import (
    HANDOFF_AGENT_GRAPH_NODE_ORDER,
    build_handoff_agent_graph,
    render_handoff_agent_graph_mermaid,
    run_handoff_agent_graph,
)
from agentic_company.agents.handoff.summary import (
    HANDOFF_SUMMARY_MARKDOWN,
    render_handoff_summary,
    write_handoff_summary,
)

__all__ = [
    "HANDOFF_AGENT_GRAPH_NODE_ORDER",
    "HANDOFF_SUMMARY_MARKDOWN",
    "HandoffAgent",
    "build_handoff_agent_graph",
    "render_handoff_summary",
    "render_handoff_agent_graph_mermaid",
    "run_handoff_agent_graph",
    "write_handoff_summary",
]
