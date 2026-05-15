import json

from agentic_company.agents.planning import run_pipeline


def test_web_app_mvp_pipeline_writes_expected_artifacts(tmp_path):
    requirements = tmp_path / "requirements.md"
    requirements.write_text(
        """# Web App MVP Requirements

Project name: Simple LLM Chat

Goal:
Create a local Streamlit app where a user can chat with an LLM.

Target user:
A solo builder testing simple assistant ideas locally.

Core features:
- User can enter a message
- App sends the message to an LLM
- App displays the assistant response

Required configuration:
- OPENAI_API_KEY
- DEFAULT_MODEL

Preferred stack:
- Python
- Streamlit

Non-goals:
- Database persistence

Acceptance criteria:
- App starts locally with Streamlit
- App starts with docker compose up --build
- User can send a message and see a response
""",
        encoding="utf-8",
    )

    output_dir = run_pipeline(requirements, tmp_path / "runs", run_id="test-run")

    assert (output_dir / "01-intake-brief.json").exists()
    assert (output_dir / "02-project-classification.json").exists()
    assert (output_dir / "03-staffing-decision.json").exists()
    assert (output_dir / "04-workflow-plan.json").exists()
    assert (output_dir / "05-implementation-brief.md").exists()
    assert (output_dir / "06-execution-request.json").exists()
    assert (output_dir / "events.jsonl").exists()

    intake = json.loads((output_dir / "01-intake-brief.json").read_text(encoding="utf-8"))
    staffing = json.loads((output_dir / "03-staffing-decision.json").read_text(encoding="utf-8"))
    workflow_plan = json.loads((output_dir / "04-workflow-plan.json").read_text(encoding="utf-8"))
    execution_request = json.loads(
        (output_dir / "06-execution-request.json").read_text(encoding="utf-8")
    )
    implementation_brief = (output_dir / "05-implementation-brief.md").read_text(encoding="utf-8")
    events = [
        json.loads(line)
        for line in (output_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert intake["project_name"] == "Simple LLM Chat"
    assert intake["required_configuration"] == ["OPENAI_API_KEY", "DEFAULT_MODEL"]
    assert "Fullstack Agent" in staffing["selected_agents"]
    assert workflow_plan["project_archetype"] == "single-service-streamlit"
    assert workflow_plan["feature_queue"][0]["id"] == "F1"
    assert workflow_plan["feature_queue"][0]["suggested_owner_agent"] == "fullstack-agent"
    assert execution_request["agent_id"] == "fullstack-agent"
    assert execution_request["provider"] == "codex"
    assert execution_request["target_project_dir"].endswith("generated-project")
    assert "05-implementation-brief.md" in execution_request["input_artifacts"]
    assert "app.py" in execution_request["expected_outputs"]
    assert "pyproject.toml" in execution_request["expected_outputs"]
    assert "uv.lock" in execution_request["expected_outputs"]
    assert "Dockerfile" in execution_request["expected_outputs"]
    assert "docker-compose.yml" in execution_request["expected_outputs"]
    assert "--mount=type=cache,target=/root/.cache/uv" in implementation_brief
    assert "uv run --no-sync" in implementation_brief
    assert any("uv-first" in item or "uv" in item for item in execution_request["instructions"])
    assert any("Docker Compose" in item for item in execution_request["instructions"])
    assert any("uv" in item for item in execution_request["constraints"])
    assert any("secrets" in item.lower() for item in execution_request["constraints"])
    assert any("install uv with pip" in item.lower() for item in execution_request["constraints"])
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_completed"
    assert any(event["data"].get("artifact") == "03-staffing-decision.json" for event in events)
    assert any(event["data"].get("artifact") == "06-execution-request.json" for event in events)
    assert any(event["event"] == "execution_request_created" for event in events)
