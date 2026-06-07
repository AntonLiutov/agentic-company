from dataclasses import dataclass
from pathlib import Path

from agentic_company.console.services import graph_artifacts


@dataclass(frozen=True)
class _Write:
    name: str
    path: Path
    changed: bool


def test_refresh_graph_artifacts_is_quiet_when_nothing_changed(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        graph_artifacts,
        "write_graph_artifacts",
        lambda root: [_Write("delivery-graph", root / "graph.mmd", False)],
    )

    graph_artifacts.refresh_graph_artifacts(tmp_path)

    assert "Refreshed LangGraph Mermaid artifacts" not in caplog.text


def test_refresh_graph_artifacts_logs_changed_graphs(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        graph_artifacts,
        "write_graph_artifacts",
        lambda root: [
            _Write("delivery-graph", root / "graphs" / "delivery.mmd", True),
            _Write("agent-map", root / "graphs" / "agents.mmd", False),
        ],
    )

    with caplog.at_level("INFO", logger=graph_artifacts.LOGGER.name):
        graph_artifacts.refresh_graph_artifacts(tmp_path)

    assert "Refreshed LangGraph Mermaid artifacts" in caplog.text
    assert "delivery-graph" in caplog.text
