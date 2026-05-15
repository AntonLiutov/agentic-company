from pathlib import Path

from agentic_company.orchestration.graphs import (
    CONSOLE_EXECUTION_NODE_ORDER,
    DELIVERY_GRAPH_NODE_ORDER,
    DeliveryGraphNodes,
    render_delivery_graph_mermaid,
    run_delivery_graph,
)
from agentic_company.platform.artifacts import artifact_ref
from agentic_company.platform.state import DeliveryState, initial_delivery_state


def test_delivery_graph_runs_linear_stage_order(tmp_path):
    visited: list[str] = []

    def node(name: str):
        def run(state: DeliveryState) -> DeliveryState:
            visited.append(name)
            return {
                **state,
                "stage": name,
                "status": f"{name}_completed",
                "completed_nodes": [*state["completed_nodes"], name],
                "artifacts": [
                    *state["artifacts"],
                    artifact_ref(
                        f"{name}.txt",
                        kind="internal",
                        owner_agent=f"{name}-agent",
                        visibility="internal",
                    ),
                ],
            }

        return run

    state = initial_delivery_state(
        run_id="graph-test",
        run_dir=tmp_path / "runs" / "graph-test",
        requirements_path=tmp_path / "requirements.md",
    )

    result = run_delivery_graph(
        state,
        nodes=DeliveryGraphNodes(
            planning=node("planning"),
            fullstack=node("fullstack"),
            qa=node("qa"),
            deployment=node("deployment"),
            handoff=node("handoff"),
        ),
    )

    assert visited == DELIVERY_GRAPH_NODE_ORDER
    assert result["completed_nodes"] == DELIVERY_GRAPH_NODE_ORDER
    assert result["stage"] == "handoff"
    assert result["status"] == "handoff_completed"
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        f"{name}.txt" for name in DELIVERY_GRAPH_NODE_ORDER
    ]


def test_initial_delivery_state_records_graph_defaults(tmp_path):
    state = initial_delivery_state(
        run_id="defaults-test",
        run_dir=Path(tmp_path, "runs", "defaults-test"),
        max_repair_attempts=3,
    )

    assert state["stage"] == "initialized"
    assert state["status"] == "initialized"
    assert state["repair_attempts"] == 0
    assert state["max_repair_attempts"] == 3
    assert state["artifacts"] == []
    assert state["blockers"] == []
    assert state["auto_confirmations"] == []
    assert state["completed_nodes"] == []


def test_delivery_graph_can_run_console_execution_subset(tmp_path):
    visited: list[str] = []

    def node(name: str):
        def run(state: DeliveryState) -> DeliveryState:
            visited.append(name)
            return {
                **state,
                "stage": name,
                "status": f"{name}_completed",
                "completed_nodes": [*state["completed_nodes"], name],
            }

        return run

    state = initial_delivery_state(
        run_id="subset-test",
        run_dir=tmp_path / "runs" / "subset-test",
    )

    result = run_delivery_graph(
        state,
        nodes=DeliveryGraphNodes(
            planning=node("planning"),
            fullstack=node("fullstack"),
            qa=node("qa"),
            deployment=node("deployment"),
            handoff=node("handoff"),
        ),
        node_order=CONSOLE_EXECUTION_NODE_ORDER,
    )

    assert visited == CONSOLE_EXECUTION_NODE_ORDER
    assert result["completed_nodes"] == CONSOLE_EXECUTION_NODE_ORDER
    assert "deployment" not in visited
    assert "handoff" not in visited


def test_delivery_graph_renders_mermaid_design():
    mermaid = render_delivery_graph_mermaid()

    assert "graph TD;" in mermaid
    assert "__start__ --> planning;" in mermaid
    assert "qa --> deployment;" in mermaid
    assert "deployment --> handoff;" in mermaid


def test_console_execution_graph_renders_mermaid_design():
    mermaid = render_delivery_graph_mermaid(node_order=CONSOLE_EXECUTION_NODE_ORDER)

    assert "__start__ --> fullstack;" in mermaid
    assert "qa --> __end__;" in mermaid
    assert "deployment --> handoff;" not in mermaid
