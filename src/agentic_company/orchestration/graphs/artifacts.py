"""Persist LangGraph Mermaid diagrams for documentation and inspection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentic_company.orchestration.graphs.rendering import (
    render_company_agent_map_mermaid,
    render_delivery_graph_mermaid,
)
from agentic_company.orchestration.graphs.routing import CONSOLE_EXECUTION_NODE_ORDER


@dataclass(frozen=True, slots=True)
class GraphArtifactSpec:
    """A generated Mermaid artifact for a platform or agent graph."""

    name: str
    relative_path: Path
    render: Callable[[], str]


@dataclass(frozen=True, slots=True)
class GraphArtifactWrite:
    """Result of refreshing one graph artifact."""

    name: str
    path: Path
    changed: bool


def graph_artifact_specs() -> list[GraphArtifactSpec]:
    """Return the currently supported graph visualization artifacts."""

    return [
        GraphArtifactSpec(
            name="delivery-graph",
            relative_path=Path(
                "src/agentic_company/orchestration/graphs/delivery-graph.mmd",
            ),
            render=lambda: render_delivery_graph_mermaid(node_order=CONSOLE_EXECUTION_NODE_ORDER),
        ),
        GraphArtifactSpec(
            name="company-agent-map",
            relative_path=Path(
                "src/agentic_company/orchestration/graphs/company-agent-map.mmd",
            ),
            render=render_company_agent_map_mermaid,
        ),
    ]


def write_graph_artifacts(root: Path) -> list[GraphArtifactWrite]:
    """Refresh Mermaid graph artifacts under the repository root."""

    writes: list[GraphArtifactWrite] = []
    for spec in graph_artifact_specs():
        path = root / spec.relative_path
        changed = _write_if_changed(path, spec.render().rstrip() + "\n")
        writes.append(GraphArtifactWrite(name=spec.name, path=path, changed=changed))
    return writes


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    path.write_text(content, encoding="utf-8")
    return changed
