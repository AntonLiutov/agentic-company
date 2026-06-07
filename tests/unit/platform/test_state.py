from agentic_company.platform.state import initial_delivery_state, mark_node_completed


def test_mark_node_completed_is_idempotent_for_retries(tmp_path) -> None:
    state = initial_delivery_state(run_id="run", run_dir=tmp_path)

    first = mark_node_completed(
        state,
        node_name="project_management",
        stage="project_management",
        status="project_management_blocked",
    )
    second = mark_node_completed(
        first,
        node_name="project_management",
        stage="project_management",
        status="project_management_completed",
    )

    assert second["completed_nodes"] == ["project_management"]
    assert second["status"] == "project_management_completed"
