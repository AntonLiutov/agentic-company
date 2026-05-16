from agentic_company.orchestration.graphs.artifacts import (
    graph_artifact_specs,
    write_graph_artifacts,
)


def test_write_graph_artifacts_persists_single_langgraph_delivery_graph(tmp_path):
    writes = write_graph_artifacts(tmp_path)

    paths = {write.path.relative_to(tmp_path).as_posix(): write for write in writes}
    assert set(paths) == {
        "src/agentic_company/orchestration/graphs/company-agent-map.mmd",
        "src/agentic_company/orchestration/graphs/delivery-graph.mmd",
    }
    assert all(write.changed for write in writes)
    content = paths["src/agentic_company/orchestration/graphs/delivery-graph.mmd"].path.read_text(
        encoding="utf-8"
    )
    assert "__start__ --> head;" in content
    assert "head --> __end__;" in content
    agent_map = paths[
        "src/agentic_company/orchestration/graphs/company-agent-map.mmd"
    ].path.read_text(encoding="utf-8")
    assert "Platform Graph Runner" in agent_map
    assert "Head Agent<br/>company coordinator" in agent_map
    assert "request_sprint_delivery" in agent_map
    assert "request_deployment" in agent_map


def test_write_graph_artifacts_tracks_unchanged_files(tmp_path):
    first = write_graph_artifacts(tmp_path)
    second = write_graph_artifacts(tmp_path)

    assert all(write.changed for write in first)
    assert not any(write.changed for write in second)


def test_graph_artifact_specs_are_named():
    names = [spec.name for spec in graph_artifact_specs()]

    assert names == [
        "delivery-graph",
        "company-agent-map",
    ]
