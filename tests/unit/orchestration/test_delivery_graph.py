from pathlib import Path

from agentic_company.orchestration.graphs import (
    CONSOLE_EXECUTION_NODE_ORDER,
    DELIVERY_GRAPH_NODE_ORDER,
    DeliveryGraphNodes,
    render_delivery_graph_mermaid,
    run_delivery_graph,
)
from agentic_company.platform.db.state import DeliveryState, initial_delivery_state


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
            head=node("head"),
            team_lead=node("team_lead"),
        ),
    )

    assert visited == DELIVERY_GRAPH_NODE_ORDER
    assert result["completed_nodes"] == DELIVERY_GRAPH_NODE_ORDER
    assert result["stage"] == "head"
    assert result["status"] == "head_completed"


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
    assert state["blockers"] == []
    assert state["auto_confirmations"] == []
    assert state["completed_nodes"] == []


def test_delivery_graph_can_run_console_execution_subset(tmp_path):
    visited: list[str] = []

    def node(name: str):
        def run(state: DeliveryState) -> DeliveryState:
            visited.append(name)
            updated: DeliveryState = {
                **state,
                "stage": name,
                "status": f"{name}_completed",
                "completed_nodes": [*state["completed_nodes"], name],
            }
            if name == "qa":
                updated["qa_status"] = "passed"
            if name == "deployment":
                updated["status"] = "deployment_deployed"
                updated["deployment_status"] = "deployed"
            return updated

        return run

    state = initial_delivery_state(
        run_id="subset-test",
        run_dir=tmp_path / "runs" / "subset-test",
    )

    result = run_delivery_graph(
        state,
        nodes=DeliveryGraphNodes(
            head=node("head"),
            team_lead=node("team_lead"),
        ),
        node_order=CONSOLE_EXECUTION_NODE_ORDER,
    )

    assert visited == CONSOLE_EXECUTION_NODE_ORDER
    assert result["completed_nodes"] == CONSOLE_EXECUTION_NODE_ORDER
    assert visited == ["head"]


def test_delivery_graph_can_run_team_lead_when_explicitly_requested(tmp_path):
    visited: list[str] = []

    def team_lead(state: DeliveryState) -> DeliveryState:
        visited.append("team_lead")
        return {
            **state,
            "stage": "team_lead",
            "status": "team_lead_sprint_handoff_ready",
            "completed_nodes": [*state["completed_nodes"], "team_lead"],
            "deployment_status": "deployed",
        }

    state = initial_delivery_state(
        run_id="feature-loop-test",
        run_dir=tmp_path / "runs" / "feature-loop-test",
    )
    result = run_delivery_graph(
        state,
        nodes=DeliveryGraphNodes(
            team_lead=team_lead,
        ),
        node_order=("team_lead",),
    )

    assert visited == ["team_lead"]
    assert result["status"] == "team_lead_sprint_handoff_ready"
    assert result["deployment_status"] == "deployed"


def test_delivery_graph_renders_mermaid_design():
    mermaid = render_delivery_graph_mermaid()

    assert "graph TD;" in mermaid
    assert "__start__ --> head;" in mermaid
    assert "head --> __end__;" in mermaid


def test_console_execution_graph_renders_mermaid_design():
    mermaid = render_delivery_graph_mermaid(node_order=CONSOLE_EXECUTION_NODE_ORDER)

    assert "__start__ --> head;" in mermaid
    assert "head --> __end__;" in mermaid
