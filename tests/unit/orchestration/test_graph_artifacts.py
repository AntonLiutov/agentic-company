from agentic_company.orchestration.graphs.artifacts import (
    graph_artifact_specs,
    write_graph_artifacts,
)


def test_write_graph_artifacts_persists_single_expanded_delivery_graph(tmp_path):
    writes = write_graph_artifacts(tmp_path)

    paths = {write.path.relative_to(tmp_path).as_posix(): write for write in writes}
    assert set(paths) == {
        "src/agentic_company/orchestration/graphs/delivery-graph.mmd",
    }
    assert all(write.changed for write in writes)
    assert "Planning Agent" in paths[
        "src/agentic_company/orchestration/graphs/delivery-graph.mmd"
    ].path.read_text(encoding="utf-8")
    content = paths["src/agentic_company/orchestration/graphs/delivery-graph.mmd"].path.read_text(
        encoding="utf-8"
    )
    assert "subgraph planning_agent[Planning Agent]" in content
    assert "subgraph fullstack_agent[Fullstack Agent]" in content
    assert "subgraph quality_agent[QA Agent]" in content
    assert "subgraph deployment_agent[Deployment Agent]" in content
    assert "subgraph handoff_agent[Handoff Agent]" in content
    assert "planning_apply_result --> fullstack_agent_entry" in content
    assert "fullstack_apply_result --> quality_agent_entry" in content
    assert "quality_apply_result --> deployment_agent_entry" in content
    assert "deployment_apply_result --> handoff_agent_entry" in content
    assert "python_checks" in content


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
