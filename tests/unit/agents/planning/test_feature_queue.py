import json
from pathlib import Path

from agentic_company.agents.planning import run_pipeline, run_planning_agent_graph
from agentic_company.agents.planning.models import FeatureWorkItem, WorkflowPhase, WorkflowPlan
from agentic_company.platform.state import initial_delivery_state


def test_feature_work_item_serializes_contract():
    feature = FeatureWorkItem(
        id="F1",
        title="Create and list tasks",
        user_value="Users can capture work.",
        acceptance_criteria=["API can create a task", "Web UI shows tasks"],
        dependencies=[],
        suggested_owner_agent="fullstack-agent",
        delivery_order=1,
        test_notes=["Check API and web flow"],
        deployment_notes=["Expose web service with API base URL"],
    )

    assert feature.to_dict() == {
        "id": "F1",
        "title": "Create and list tasks",
        "user_value": "Users can capture work.",
        "acceptance_criteria": ["API can create a task", "Web UI shows tasks"],
        "dependencies": [],
        "suggested_owner_agent": "fullstack-agent",
        "delivery_order": 1,
        "test_notes": ["Check API and web flow"],
        "deployment_notes": ["Expose web service with API base URL"],
    }


def test_workflow_plan_serializes_feature_queue():
    plan = WorkflowPlan(
        workflow_id="multi-service-api-web-mvp",
        project_name="Task Tracker",
        project_archetype="api-web-compose",
        phases=[WorkflowPhase(name="Implementation", owner="Fullstack Agent", outputs=["API"])],
        feature_queue=[
            FeatureWorkItem(
                id="F1",
                title="Create and list tasks",
                user_value="Users can capture work.",
                acceptance_criteria=["API can create a task"],
                dependencies=[],
                suggested_owner_agent="fullstack-agent",
                delivery_order=1,
                test_notes=["Check API"],
                deployment_notes=["Deploy API and web"],
            )
        ],
    )

    payload = plan.to_dict()

    assert payload["project_archetype"] == "api-web-compose"
    assert payload["feature_queue"][0]["id"] == "F1"
    assert payload["feature_queue"][0]["delivery_order"] == 1


def test_multi_service_requirements_produce_feature_queue(tmp_path):
    requirements = Path("examples/requirements/multi-service-task-tracker.md")

    output_dir = run_pipeline(requirements, tmp_path / "runs", run_id="multi-service-test")

    workflow_plan = json.loads((output_dir / "04-workflow-plan.json").read_text(encoding="utf-8"))
    classification = json.loads(
        (output_dir / "02-project-classification.json").read_text(encoding="utf-8")
    )
    staffing = json.loads((output_dir / "03-staffing-decision.json").read_text(encoding="utf-8"))
    intake = json.loads((output_dir / "01-intake-brief.json").read_text(encoding="utf-8"))
    execution_request = json.loads(
        (output_dir / "06-execution-request.json").read_text(encoding="utf-8")
    )
    implementation_brief = (output_dir / "05-implementation-brief.md").read_text(encoding="utf-8")

    assert intake["required_configuration"] == []
    assert classification["project_type"] == "multi-service-web-app-mvp"
    assert classification["complexity"] == "medium"
    assert classification["delivery_mode"] == "lean-dev-cloud-mvp"
    assert "Deployment Agent" in staffing["selected_agents"]
    assert workflow_plan["workflow_id"] == "multi-service-api-web-mvp"
    assert workflow_plan["project_archetype"] == "api-web-compose"
    assert [feature["id"] for feature in workflow_plan["feature_queue"]] == ["F1", "F2"]
    assert workflow_plan["feature_queue"][0]["title"] == (
        "Create and list tasks through the API and web UI"
    )
    assert workflow_plan["feature_queue"][1]["acceptance_criteria"] == [
        "API can mark a task as done",
        "Web UI can toggle a task between open and done",
    ]
    assert "F1: Create and list tasks through the API and web UI" in implementation_brief
    assert "F2: Mark tasks done through the API and web UI" in implementation_brief
    assert "`api-web-compose`" in implementation_brief
    assert "API_BASE_URL=http://api:8000" in implementation_brief
    assert "agentic-task-tracker-api:latest" in implementation_brief
    assert "agentic-task-tracker-web:latest" in implementation_brief
    assert "agentic-task-tracker-api" in implementation_brief
    assert "agentic-task-tracker-web" in implementation_brief
    assert "DEFAULT_MODEL" not in implementation_brief
    assert "gpt-4o-mini" not in implementation_brief
    assert "api/app.py" in execution_request["expected_outputs"]
    assert "web/app.py" in execution_request["expected_outputs"]
    assert "Dockerfile.api" in execution_request["expected_outputs"]
    assert "Dockerfile.web" in execution_request["expected_outputs"]
    assert execution_request["project_archetype"] == "api-web-compose"
    assert execution_request["active_feature"]["id"] == "F1"
    assert [feature["id"] for feature in execution_request["feature_queue"]] == ["F1", "F2"]
    assert any("api and web" in item.lower() for item in execution_request["instructions"])
    assert any(
        "agentic-task-tracker-api:latest" in item for item in execution_request["instructions"]
    )
    assert any("agentic-task-tracker-api" in item for item in execution_request["instructions"])


def test_workflow_plan_schema_covers_feature_queue_contract():
    schema = json.loads(
        Path(
            "src/agentic_company/agents/planning/schemas/workflow-plan.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert "feature_queue" in schema["required"]
    assert "project_archetype" in schema["required"]
    feature_schema = schema["properties"]["feature_queue"]["items"]
    assert set(feature_schema["required"]) == {
        "id",
        "title",
        "user_value",
        "acceptance_criteria",
        "dependencies",
        "suggested_owner_agent",
        "delivery_order",
        "test_notes",
        "deployment_notes",
    }
    assert feature_schema["additionalProperties"] is False


def test_planning_graph_prepares_feature_queue_for_fullstack_iteration(tmp_path):
    requirements = Path("examples/requirements/multi-service-task-tracker.md")
    run_dir = tmp_path / "runs" / "planning-gate"
    state = initial_delivery_state(
        run_id="planning-gate",
        run_dir=run_dir,
        requirements_path=requirements,
    )

    result = run_planning_agent_graph(state, run_pipeline)

    assert result["completed_nodes"] == ["planning"]
    assert result["stage"] == "planning"
    assert result["status"] == "planning_feature_queue_ready"
    assert result["project_archetype"] == "api-web-compose"
    assert result["active_feature_id"] == "F1"
    assert result["completed_feature_ids"] == []
    assert [feature["id"] for feature in result["feature_queue"]] == ["F1", "F2"]
    assert result["blockers"] == []
