"""Console service for refreshing checked-in graph diagrams."""

from __future__ import annotations

import logging
from pathlib import Path

from agentic_company.orchestration.graphs import write_graph_artifacts

LOGGER = logging.getLogger(__name__)


def refresh_graph_artifacts(root: Path) -> None:
    """Refresh LangGraph Mermaid artifacts once for the current process root."""

    writes = write_graph_artifacts(root)
    changed = [write for write in writes if write.changed]
    if not changed:
        return

    LOGGER.info(
        "Refreshed LangGraph Mermaid artifacts artifacts=%s changed=%s",
        [str(write.path.relative_to(root)) for write in writes],
        [write.name for write in changed],
    )
