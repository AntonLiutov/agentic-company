import json

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.orchestration.graphs import (
    CONSOLE_EXECUTION_NODE_ORDER,
    DELIVERY_GRAPH_NODE_ORDER,
    DeliveryGraphNodes,
)
from agentic_company.orchestration.runtime import (
    DEFAULT_STATE_FILENAME,
    DeliveryGraphRuntime,
)
from agentic_company.platform.run_trace import load_run_events
from agentic_company.platform.state import DeliveryState, initial_delivery_state


def test_delivery_graph_runtime_starts_graph_and_persists_state(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "runtime-test"
    _create_run(tmp_path, monkeypatch, run_dir)
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
            }

        return run

    runtime = DeliveryGraphRuntime(
        nodes=DeliveryGraphNodes(
            head=node("head"),
            team_lead=node("team_lead"),
        )
    )

    result = runtime.start(run_dir, requirements_path=requirements_path)

    state_path = run_dir / DEFAULT_STATE_FILENAME
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert visited == DELIVERY_GRAPH_NODE_ORDER
    assert result["run_id"] == "runtime-test"
    assert result["requirements_path"] == str(requirements_path)
    assert result["stage"] == "head"
    assert result["status"] == "head_completed"
    assert persisted == result
    assert runtime.load_state(run_dir) == result


def test_delivery_graph_runtime_loads_existing_state_before_running(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "existing-state"
    _create_run(tmp_path, monkeypatch, run_dir)
    starting_state = initial_delivery_state(run_id="existing-state", run_dir=run_dir)
    runtime = DeliveryGraphRuntime(
        node_order=("head",),
        nodes=DeliveryGraphNodes(
            head=lambda state: {
                **state,
                "stage": "head",
                "status": "head_delivery_completed",
                "completed_nodes": [*state["completed_nodes"], "head"],
            },
        ),
    )
    runtime.save_state(run_dir, starting_state)

    result = runtime.start(
        run_dir,
        run_id="ignored-new-id",
        max_repair_attempts=5,
    )

    assert result["run_id"] == "existing-state"
    assert result["max_repair_attempts"] == 5
    assert result["completed_nodes"] == ["head"]


def test_delivery_graph_runtime_writes_graph_events(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "graph-events"
    _create_run(tmp_path, monkeypatch, run_dir)
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
            head=node("head"),
            team_lead=node("team_lead"),
        ),
    )

    runtime.start(run_dir)

    events = load_run_events(run_dir)
    event_names = [event.event_type for event in events]
    node_events = [event for event in events if event.event_type == "delivery_graph_node_completed"]

    assert visited == CONSOLE_EXECUTION_NODE_ORDER
    assert "delivery_graph_started" in event_names
    assert "delivery_graph_completed" in event_names
    assert "delivery_graph_state_written" in event_names
    assert [event.data["node"] for event in node_events] == CONSOLE_EXECUTION_NODE_ORDER
    assert {event.agent_id for event in events} == {"delivery-graph"}


def test_delivery_graph_runtime_hydrates_only_execution_session_fields(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "hydrated"
    run_dir.mkdir(parents=True)
    _create_run(tmp_path, monkeypatch, run_dir, target_project_dir=run_dir / "generated-project")
    request_path = run_dir / "delivery/execution-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": "hydrated",
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "target_project_dir": str(run_dir / "generated-project"),
                "input_artifacts": [],
                "expected_outputs": [],
                "instructions": [],
                "constraints": [],
                "work_item": {"work_item_id": "F1"},
                "completed_work_item_ids": [],
            }
        ),
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def team_lead(state: DeliveryState) -> DeliveryState:
        seen["target_project_dir"] = state["target_project_dir"]
        seen["has_work_items"] = "work_items" in state
        seen["has_current_work_item_id"] = "current_work_item_id" in state
        return {**state, "stage": "team_lead", "status": "seen"}

    runtime = DeliveryGraphRuntime(
        node_order=("team_lead",),
        nodes=DeliveryGraphNodes(team_lead=team_lead),
    )

    runtime.start(run_dir)

    assert seen["target_project_dir"] == str(run_dir / "generated-project")
    assert seen["has_work_items"] is False
    assert seen["has_current_work_item_id"] is False


def test_delivery_graph_runtime_checkpoints_state_between_nodes(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "checkpointed"
    _create_run(tmp_path, monkeypatch, run_dir)

    def team_lead(state: DeliveryState) -> DeliveryState:
        return {
            **state,
            "stage": "team_lead",
            "status": "team_lead_sprint_handoff_ready",
            "qa_status": "passed",
            "deployment_status": "deployed",
            "completed_nodes": [*state["completed_nodes"], "team_lead"],
        }

    runtime = DeliveryGraphRuntime(
        node_order=("team_lead",),
        nodes=DeliveryGraphNodes(
            team_lead=team_lead,
        ),
    )

    result = runtime.start(run_dir)

    assert result["status"] == "team_lead_sprint_handoff_ready"


def test_delivery_graph_runtime_skips_downstream_nodes_when_state_has_blockers(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "runs" / "blocked-after-business-analysis"
    _create_run(tmp_path, monkeypatch, run_dir)
    visited: list[str] = []

    def business_analyst(state: DeliveryState) -> DeliveryState:
        visited.append("business_analyst")
        return {
            **state,
            "stage": "business_analysis",
            "status": "business_analysis_verified_downstream_paused",
            "blockers": ["Downstream agents are intentionally paused."],
            "completed_nodes": [*state["completed_nodes"], "business_analyst"],
        }

    def downstream(name: str):
        def run(state: DeliveryState) -> DeliveryState:
            visited.append(name)
            return state

        return run

    runtime = DeliveryGraphRuntime(
        node_order=("business_analyst", "team_lead"),
        nodes=DeliveryGraphNodes(
            business_analyst=business_analyst,
            team_lead=downstream("team_lead"),
        ),
    )

    result = runtime.start(run_dir)

    events = load_run_events(run_dir)
    skipped = [event for event in events if event.event_type == "delivery_graph_node_skipped"]

    assert visited == ["business_analyst"]
    assert result["stage"] == "business_analysis"
    assert result["status"] == "business_analysis_verified_downstream_paused"
    assert result["blockers"] == ["Downstream agents are intentionally paused."]
    assert [event.data["node"] for event in skipped] == ["team_lead"]


def _create_run(
    tmp_path,
    monkeypatch,
    run_dir,
    *,
    target_project_dir=None,
) -> None:
    db_path = tmp_path / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email=f"{run_dir.name}@example.test",
        username=f"user-{run_dir.name}",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Runtime",
        request_text="Runtime",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    repo.create_run(
        project_id=project.id,
        run_uid=run_dir.name,
        run_dir=run_dir,
        target_project_dir=target_project_dir or run_dir / "generated-project",
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
