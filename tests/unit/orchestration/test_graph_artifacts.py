from agentic_company.orchestration.graphs.artifacts import (
    graph_artifact_specs,
    write_graph_artifacts,
)


def test_write_graph_artifacts_persists_single_langgraph_delivery_graph(tmp_path):
    writes = write_graph_artifacts(tmp_path)

    paths = {write.path.relative_to(tmp_path).as_posix(): write for write in writes}
    assert set(paths) == {
        "src/agentic_company/orchestration/graphs/delivery-graph.mmd",
    }
    assert all(write.changed for write in writes)
    content = paths[
        "src/agentic_company/orchestration/graphs/delivery-graph.mmd"
    ].path.read_text(encoding="utf-8")
    assert "__start__ --> fullstack;" in content
    assert "fullstack -.-> qa;" in content
    assert "qa -.-> fullstack;" in content
    assert "qa -.-> deployment;" in content
    assert "qa -. &nbsp;end&nbsp; .-> __end__;" in content
    assert "deployment -.-> handoff;" in content
    assert "handoff --> __end__;" in content


def test_write_graph_artifacts_tracks_unchanged_files(tmp_path):
    first = write_graph_artifacts(tmp_path)
    second = write_graph_artifacts(tmp_path)

    assert all(write.changed for write in first)
    assert not any(write.changed for write in second)


def test_graph_artifact_specs_are_named():
    names = [spec.name for spec in graph_artifact_specs()]

    assert names == [
        "delivery-graph",
    ]
