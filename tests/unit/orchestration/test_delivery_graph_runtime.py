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


def test_delivery_graph_runtime_hydrates_feature_queue_from_existing_execution_request(
    tmp_path,
):
    run_dir = tmp_path / "runs" / "hydrated"
    run_dir.mkdir(parents=True)
    feature_queue = [
        {"id": "F1", "title": "Create", "delivery_order": 1},
        {"id": "F2", "title": "Update", "delivery_order": 2},
    ]
    (run_dir / "06-execution-request.json").write_text(
        json.dumps(
            {
                "run_id": "hydrated",
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.5",
                "target_project_dir": str(run_dir / "generated-project"),
                "input_artifacts": [],
                "expected_outputs": [],
                "instructions": [],
                "constraints": [],
                "project_archetype": "api-web-compose",
                "feature_queue": feature_queue,
                "active_feature": feature_queue[0],
                "completed_feature_ids": [],
            }
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fullstack(state: DeliveryState) -> DeliveryState:
        seen["project_archetype"] = state["project_archetype"]
        seen["feature_queue"] = state["feature_queue"]
        seen["active_feature_id"] = state["active_feature_id"]
        seen["feature_statuses"] = state["feature_statuses"]
        seen["feature_repair_attempts"] = state["feature_repair_attempts"]
        return {**state, "stage": "fullstack", "status": "seen"}

    runtime = DeliveryGraphRuntime(
        node_order=CONSOLE_EXECUTION_NODE_ORDER,
        nodes=DeliveryGraphNodes(fullstack=fullstack, qa=lambda state: state),
    )

    runtime.start(run_dir)

    assert seen["project_archetype"] == "api-web-compose"
    assert seen["feature_queue"] == feature_queue
    assert seen["active_feature_id"] == "F1"
    assert seen["feature_statuses"] == {}
    assert seen["feature_repair_attempts"] == {}


def test_delivery_graph_runtime_checkpoints_state_between_nodes(tmp_path):
    run_dir = tmp_path / "runs" / "checkpointed"
    seen_by_deployment: dict[str, object] = {}

    def fullstack(state: DeliveryState) -> DeliveryState:
        return {
            **state,
            "stage": "fullstack",
            "status": "fullstack_feature_implemented",
            "completed_nodes": [*state["completed_nodes"], "fullstack"],
        }

    def qa(state: DeliveryState) -> DeliveryState:
        return {
            **state,
            "stage": "qa",
            "status": "feature_queue_qa_completed_deployment_ready",
            "active_feature_id": None,
            "completed_feature_ids": ["F1", "F2"],
            "feature_statuses": {"F1": "qa_passed", "F2": "qa_passed"},
            "project_archetype": "api-web-compose",
            "qa_status": "passed",
            "completed_nodes": [*state["completed_nodes"], "qa:F2"],
        }

    def deployment(state: DeliveryState) -> DeliveryState:
        persisted = json.loads((run_dir / DEFAULT_STATE_FILENAME).read_text(encoding="utf-8"))
        seen_by_deployment["completed_feature_ids"] = persisted["completed_feature_ids"]
        return {
            **state,
            "stage": "deployment",
            "status": "deployment_deployed",
            "deployment_status": "deployed",
            "completed_nodes": [*state["completed_nodes"], "deployment"],
        }

    def handoff(state: DeliveryState) -> DeliveryState:
        return {
            **state,
            "stage": "handoff",
            "status": "handoff_ready",
            "completed_nodes": [*state["completed_nodes"], "handoff"],
        }

    runtime = DeliveryGraphRuntime(
        node_order=CONSOLE_EXECUTION_NODE_ORDER,
        nodes=DeliveryGraphNodes(
            fullstack=fullstack,
            qa=qa,
            deployment=deployment,
            handoff=handoff,
        ),
    )

    result = runtime.start(run_dir)

    assert seen_by_deployment["completed_feature_ids"] == ["F1", "F2"]
    assert result["completed_feature_ids"] == ["F1", "F2"]


def test_delivery_graph_runtime_skips_downstream_nodes_when_state_has_blockers(tmp_path):
    run_dir = tmp_path / "runs" / "blocked-after-planning"
    visited: list[str] = []

    def planning(state: DeliveryState) -> DeliveryState:
        visited.append("planning")
        return {
            **state,
            "stage": "planning",
            "status": "planning_verified_downstream_paused",
            "blockers": ["Downstream agents are intentionally paused."],
            "completed_nodes": [*state["completed_nodes"], "planning"],
        }

    def downstream(name: str):
        def run(state: DeliveryState) -> DeliveryState:
            visited.append(name)
            return state

        return run

    runtime = DeliveryGraphRuntime(
        nodes=DeliveryGraphNodes(
            planning=planning,
            fullstack=downstream("fullstack"),
            qa=downstream("qa"),
            deployment=downstream("deployment"),
            handoff=downstream("handoff"),
        )
    )

    result = runtime.start(run_dir)

    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    skipped = [event for event in events if event["event"] == "delivery_graph_node_skipped"]

    assert visited == ["planning"]
    assert result["stage"] == "planning"
    assert result["status"] == "planning_verified_downstream_paused"
    assert result["blockers"] == ["Downstream agents are intentionally paused."]
    assert [event["data"]["node"] for event in skipped] == [
        "fullstack",
        "qa",
        "deployment",
        "handoff",
    ]
