import json

from agentic_company.orchestration.graphs import (
    CONSOLE_EXECUTION_NODE_ORDER,
    DELIVERY_GRAPH_NODE_ORDER,
    DeliveryGraphNodes,
)
from agentic_company.orchestration.runtime import (
    DEFAULT_STATE_FILENAME,
    DeliveryGraphRuntime,
)
from agentic_company.platform.artifacts import artifact_ref
from agentic_company.platform.state import DeliveryState, initial_delivery_state


def test_delivery_graph_runtime_starts_graph_and_persists_state(tmp_path):
    run_dir = tmp_path / "runs" / "runtime-test"
    requirements_path = run_dir / "00-requirements.md"
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
                        f"{name}.json",
                        kind="internal",
                        owner_agent=f"{name}-agent",
                        visibility="internal",
                    ),
                ],
            }

        return run

    runtime = DeliveryGraphRuntime(
        nodes=DeliveryGraphNodes(
            planning=node("planning"),
            fullstack=node("fullstack"),
            qa=node("qa"),
            deployment=node("deployment"),
            handoff=node("handoff"),
        )
    )

    result = runtime.start(run_dir, requirements_path=requirements_path)

    state_path = run_dir / DEFAULT_STATE_FILENAME
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert visited == DELIVERY_GRAPH_NODE_ORDER
    assert result["run_id"] == "runtime-test"
    assert result["requirements_path"] == str(requirements_path)
    assert result["stage"] == "handoff"
    assert result["status"] == "handoff_completed"
    assert persisted == result
    assert runtime.load_state(run_dir) == result


def test_delivery_graph_runtime_loads_existing_state_before_running(tmp_path):
    run_dir = tmp_path / "runs" / "existing-state"
    starting_state = initial_delivery_state(run_id="existing-state", run_dir=run_dir)
    starting_state["artifacts"] = [
        artifact_ref(
            "existing.json",
            kind="internal",
            owner_agent="existing-agent",
            visibility="internal",
        )
    ]
    runtime = DeliveryGraphRuntime(
        nodes=DeliveryGraphNodes(
            planning=lambda state: {
                **state,
                "stage": "planning",
                "status": "planning_completed",
                "completed_nodes": [*state["completed_nodes"], "planning"],
            },
            fullstack=lambda state: state,
            qa=lambda state: state,
            deployment=lambda state: state,
            handoff=lambda state: state,
        )
    )
    runtime.save_state(run_dir, starting_state)

    result = runtime.start(
        run_dir,
        run_id="ignored-new-id",
        max_repair_attempts=5,
    )

    assert result["run_id"] == "existing-state"
    assert result["max_repair_attempts"] == 3
    assert result["completed_nodes"] == ["planning"]
    assert result["artifacts"] == starting_state["artifacts"]


def test_delivery_graph_runtime_writes_graph_events(tmp_path):
    run_dir = tmp_path / "runs" / "graph-events"
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

    runtime = DeliveryGraphRuntime(
        node_order=CONSOLE_EXECUTION_NODE_ORDER,
        nodes=DeliveryGraphNodes(
            planning=node("planning"),
            fullstack=node("fullstack"),
            qa=node("qa"),
            deployment=node("deployment"),
            handoff=node("handoff"),
        ),
    )

    runtime.start(run_dir)

    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event_names = [event["event"] for event in events]
    node_events = [event for event in events if event["event"] == "delivery_graph_node_completed"]

    assert visited == CONSOLE_EXECUTION_NODE_ORDER
    assert "delivery_graph_started" in event_names
    assert "delivery_graph_completed" in event_names
    assert "delivery_graph_state_written" in event_names
    assert [event["data"]["node"] for event in node_events] == CONSOLE_EXECUTION_NODE_ORDER
    assert {event["agent_id"] for event in events} == {"delivery-graph"}
