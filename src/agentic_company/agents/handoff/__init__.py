"""Documentation / Handoff Agent implementation package."""

from agentic_company.agents.handoff.agent import HandoffAgent
from agentic_company.agents.handoff.codex_cli import (
    HANDOFF_CODEX_AGENT_ID,
    HANDOFF_EVIDENCE_JSON,
    HANDOFF_REPORT_HTML,
    HANDOFF_SUMMARY_MARKDOWN,
    HandoffCodexRunner,
    build_handoff_codex_prompt,
    read_handoff_contract,
)
from agentic_company.agents.handoff.graph import (
    HANDOFF_AGENT_GRAPH_NODE_ORDER,
    build_handoff_agent_graph,
    render_handoff_agent_graph_mermaid,
    run_handoff_agent_graph,
)

__all__ = [
    "HANDOFF_CODEX_AGENT_ID",
    "HANDOFF_EVIDENCE_JSON",
    "HANDOFF_REPORT_HTML",
    "HANDOFF_AGENT_GRAPH_NODE_ORDER",
    "HANDOFF_SUMMARY_MARKDOWN",
    "HandoffCodexRunner",
    "HandoffAgent",
    "build_handoff_codex_prompt",
    "build_handoff_agent_graph",
    "read_handoff_contract",
    "render_handoff_agent_graph_mermaid",
    "run_handoff_agent_graph",
]
