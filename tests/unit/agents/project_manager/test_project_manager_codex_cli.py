import json
import subprocess
from pathlib import Path

from agentic_company.agents.project_manager.codex_cli import (
    PROJECT_MANAGER_WORK_DIR,
    ProjectManagerCodexRunner,
    build_project_management_codex_prompt,
)
from agentic_company.agents.project_manager.graph import (
    ARCHITECTURE_JSON,
    ARCHITECTURE_MD,
    ARCHITECTURE_MMD,
    BUSINESS_ANALYSIS_JSON,
    BUSINESS_ANALYSIS_MD,
    PROJECT_MANAGEMENT_JSON,
    PROJECT_MANAGEMENT_MD,
    PROJECT_MANAGEMENT_REQUEST,
    PROJECT_MANAGEMENT_RISKS_MD,
    PROJECT_MANAGEMENT_ROADMAP_CSV,
    PROJECT_MANAGEMENT_WORK_ITEMS_JSON,
)
from agentic_company.console.web.db import ConsoleRepository


def test_project_management_prompt_scopes_codex_to_planning_artifacts(tmp_path, monkeypatch):
    _register_run(tmp_path, monkeypatch)
    _write_inputs(tmp_path)
    request = {
        "run_id": "run",
        "model": "gpt-5.3-codex",
        "requirements_artifact": "00-requirements.md",
        "input_artifacts": [
            BUSINESS_ANALYSIS_MD,
            BUSINESS_ANALYSIS_JSON,
            ARCHITECTURE_MD,
            ARCHITECTURE_JSON,
            ARCHITECTURE_MMD,
        ],
        "expected_outputs": [
            PROJECT_MANAGEMENT_MD,
            PROJECT_MANAGEMENT_JSON,
            PROJECT_MANAGEMENT_WORK_ITEMS_JSON,
            PROJECT_MANAGEMENT_RISKS_MD,
            PROJECT_MANAGEMENT_ROADMAP_CSV,
        ],
        "planning_policy": {
            "sprint_count_guidance": (
                "Choose the natural sprint breakdown without default counts, "
                "numeric quotas, caps, or orientational ranges."
            ),
            "feature_sizing_guidance": "Keep features as meaningful vertical slices.",
            "sprint_capacity_guidance": (
                "Size each sprint by total risk, effort, dependency coupling, QA "
                "burden, and deployment/release complexity rather than item count."
            ),
            "planning_bias": "minimum sufficient delivery plan without artificial expansion",
        },
        "incoming_messages": (
            "- Message id: msg-head\n  From: head-agent\n  Content:\n    Plan the release."
        ),
        "available_agents": [
            {
                "agent_id": "project-manager-agent",
                "name": "Project Manager Agent",
                "stage": "project_management",
                "family": "planning-delivery",
                "runtime": "L4 LangGraph Agent Executor + L6 Codex Project Manager",
            },
            {
                "agent_id": "team-lead-agent",
                "name": "Team Lead Agent",
                "stage": "team_lead",
                "family": "delivery",
                "runtime": "L4 LangGraph Agent Executor",
            },
        ],
    }

    prompt = build_project_management_codex_prompt(request, tmp_path)

    assert PROJECT_MANAGEMENT_MD in prompt
    assert PROJECT_MANAGEMENT_JSON in prompt
    assert PROJECT_MANAGEMENT_WORK_ITEMS_JSON in prompt
    assert PROJECT_MANAGEMENT_RISKS_MD in prompt
    assert PROJECT_MANAGEMENT_ROADMAP_CSV in prompt
    assert "Write only Project Manager artifacts" in prompt
    assert "Do not implement code" in prompt
    assert "PM-to-runtime materialization contract" in prompt
    assert "sprint_count_guidance" in prompt
    assert "feature_sizing_guidance" in prompt
    assert "sprint_capacity_guidance" in prompt
    assert "planning_bias" in prompt
    assert "Scale planning ceremony to the source complexity" in prompt
    assert "simple demo app should get\n  a compact release plan" in prompt
    assert "strong Codex-backed specialist agents" in prompt
    assert "Prefer vertical user-visible delivery slices" in prompt
    assert "not split backend, frontend, QA, deployment, or documentation" in prompt
    assert "plan for deployed access\n  by default" in prompt
    assert "A working URL is part of delivery" in prompt
    assert "do not derive a default sprint count, task count, quota" in prompt
    assert "natural release structure" in prompt
    assert "as many or as few sprints and features" in prompt
    assert "hidden numeric defaults" in prompt
    assert "complex apps may need 3-5 or more" not in prompt
    assert "medium tasks often need 2-3" not in prompt
    assert "minimum sufficient sprint count" not in prompt
    assert "do not compress complex\n  scope into overloaded sprints" in prompt
    assert "user journey completeness rather than by item count" in prompt
    assert "Suggested numbers are subjective orientation only" not in prompt
    assert "one large or high-risk feature" not in prompt
    assert "do not put multiple high-risk or high-unknown features" in prompt
    assert "a sprint may be narrow or broad" in prompt
    assert "Available agent registry snapshot" in prompt
    assert "project-manager-agent: Project Manager Agent" in prompt
    assert "team-lead-agent: Team Lead Agent" in prompt
    assert "Head Agent coordinates this planning flow" in prompt
    assert "Azure deployment is a supported platform capability" in prompt
    assert "plan it as a real\n  sprint/release deployment gate" in prompt
    assert "plan deployment as executable current-release work" in prompt
    assert "Incoming coordinator messages" in prompt
    assert "Plan the release" in prompt
    assert "Use the registry snapshot only as context for internal JSON" in prompt
    assert "Do not treat it\nas an exhaustive future limit" in prompt
    assert "planned-work-items.json" in prompt
    assert "roadmap.csv" in prompt
    assert "Excel/Sheets-friendly roadmap table" in prompt
    assert "sprint_id, work_item_id, title, goal" in prompt
    assert "Every work item must include" in prompt
    assert "Do not set executable current-release work to `blocked`" in prompt
    assert "use `deployment-agent` for deployment" in prompt
    assert "Do not use fake sprint ids such as `future-p1`" in prompt
    assert "Use canonical sprint ids consistently" in prompt
    assert "zero-padded `sprint-XX` ids" in prompt
    assert "Do not use alternate ids such as\n  `S1`, `S2`, `Sprint 1`" in prompt
    assert "must match exactly so Head and Team Lead can route one sprint at a time" in prompt
    assert "Preserve every distinct feature/source label" in prompt
    assert "do not split one source feature into multiple work item ids" in prompt
    assert "release_gates must be a machine-readable array" in prompt
    assert "include a final deployment gate" in prompt
    assert "include a planned work item such as `DEPLOY`" in prompt
    assert "M2-DEPLOY" not in prompt
    assert 'suggested_owner_agent: "deployment-agent"' in prompt
    assert "give Deployment Agent freedom" in prompt


def test_project_manager_codex_runner_maps_valid_contract_to_completed_result(
    tmp_path, monkeypatch
):
    _register_run(tmp_path, monkeypatch)
    _write_inputs(tmp_path)
    request_path = tmp_path / PROJECT_MANAGEMENT_REQUEST
    request_path.write_text(
        json.dumps(
            {
                "run_id": "run",
                "agent_id": "project-manager-agent",
                "model": "gpt-5.3-codex",
                "requirements_artifact": "00-requirements.md",
                "input_artifacts": [
                    BUSINESS_ANALYSIS_MD,
                    BUSINESS_ANALYSIS_JSON,
                    ARCHITECTURE_MD,
                    ARCHITECTURE_JSON,
                    ARCHITECTURE_MMD,
                ],
                "expected_outputs": [
                    PROJECT_MANAGEMENT_MD,
                    PROJECT_MANAGEMENT_JSON,
                    PROJECT_MANAGEMENT_WORK_ITEMS_JSON,
                    PROJECT_MANAGEMENT_RISKS_MD,
                    PROJECT_MANAGEMENT_ROADMAP_CSV,
                ],
                "planning_policy": {
                    "sprint_count_guidance": (
                        "Choose the natural sprint breakdown without default counts, "
                        "numeric quotas, caps, or orientational ranges."
                    ),
                    "feature_sizing_guidance": "Keep features as meaningful vertical slices.",
                    "sprint_capacity_guidance": (
                        "Size each sprint by total risk, effort, dependency coupling, QA burden, "
                        "and deployment/release complexity rather than item count."
                    ),
                    "planning_bias": (
                        "minimum sufficient delivery plan without artificial expansion"
                    ),
                },
                "codex_resume_thread_id": "",
                "available_agents": [],
                "incoming_messages": "- No incoming coordinator messages were provided.",
            }
        ),
        encoding="utf-8",
    )

    def fake_command(
        command,
        prompt,
        timeout_seconds,
        log_path: Path,
        raw_events_path: Path,
    ):
        output_dir = tmp_path / "upstream-planning" / "project-management"
        output_dir.mkdir(parents=True, exist_ok=True)
        (tmp_path / PROJECT_MANAGEMENT_MD).write_text("# Release Plan\n", encoding="utf-8")
        (tmp_path / PROJECT_MANAGEMENT_RISKS_MD).write_text("# Risks\n", encoding="utf-8")
        (tmp_path / PROJECT_MANAGEMENT_ROADMAP_CSV).write_text(
            "sprint_id,work_item_id,title,goal,dependencies,owner_agent,qa_focus,"
            "deployment_note,status\n"
            "sprint-01,F1,Create tasks,Ship MVP,,fullstack-agent,API and UI,"
            "Deploy after QA,pending\n",
            encoding="utf-8",
        )
        feature = _feature()
        (tmp_path / PROJECT_MANAGEMENT_WORK_ITEMS_JSON).write_text(
            json.dumps([feature]),
            encoding="utf-8-sig",
        )
        (output_dir / "sprint-01-plan.json").write_text(
            json.dumps(
                {
                    "sprint_id": "sprint-01",
                    "title": "MVP baseline",
                    "goal": "Create the first usable release.",
                    "features": [feature],
                    "exit_criteria": ["Feature QA passes."],
                    "deployment_policy": "Deploy after sprint QA.",
                    "is_final_sprint": True,
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / PROJECT_MANAGEMENT_JSON).write_text(
            json.dumps(
                {
                    "release_goal": "Ship a small task tracker.",
                    "planning_policy": {},
                    "sprint_count": 1,
                    "sprints": [{"sprint_id": "sprint-01"}],
                    "planned_work_items": [feature],
                    "release_gates": [],
                    "dependencies": [],
                    "risks": [],
                    "open_questions": [],
                    "assumptions": [],
                    "team_lead_contract": {},
                    "coordination_notes": [],
                    "source_traceability": [],
                }
            ),
            encoding="utf-8",
        )
        log_path.write_text("done\n", encoding="utf-8")
        raw_events_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="Project management complete.")

    result = ProjectManagerCodexRunner(command_executor=fake_command).run(tmp_path)

    assert result.status == "project_management_completed"
    assert PROJECT_MANAGEMENT_MD in result.output_artifacts
    assert PROJECT_MANAGEMENT_JSON in result.output_artifacts
    assert PROJECT_MANAGEMENT_WORK_ITEMS_JSON in result.output_artifacts
    assert PROJECT_MANAGEMENT_RISKS_MD in result.output_artifacts
    assert PROJECT_MANAGEMENT_ROADMAP_CSV in result.output_artifacts
    assert "upstream-planning/project-management/sprint-01-plan.json" in result.output_artifacts
    assert any(
        artifact.startswith((PROJECT_MANAGER_WORK_DIR / "codex").as_posix())
        for artifact in result.output_artifacts
    )
    assert result.blocking_findings == []
    assert (
        not (tmp_path / PROJECT_MANAGEMENT_WORK_ITEMS_JSON)
        .read_text(encoding="utf-8")
        .startswith("\ufeff")
    )


def _write_inputs(run_dir: Path) -> None:
    (run_dir / "00-requirements.md").write_text(
        "F1: Create and list tasks.\nF2: Mark tasks done.\n",
        encoding="utf-8",
    )
    (run_dir / BUSINESS_ANALYSIS_MD).parent.mkdir(parents=True, exist_ok=True)
    (run_dir / BUSINESS_ANALYSIS_MD).write_text("# Business Analysis\n", encoding="utf-8")
    (run_dir / BUSINESS_ANALYSIS_JSON).write_text(
        json.dumps({"product_goal": "Track tasks", "source_refs": ["F1", "F2"]}),
        encoding="utf-8",
    )
    (run_dir / ARCHITECTURE_MD).write_text("# Architecture\n", encoding="utf-8")
    (run_dir / ARCHITECTURE_JSON).write_text(
        json.dumps({"architecture_goal": "Two service prototype."}),
        encoding="utf-8",
    )
    (run_dir / ARCHITECTURE_MMD).write_text(
        "flowchart LR\n  User --> Web\n",
        encoding="utf-8",
    )


def _feature() -> dict[str, object]:
    return {
        "id": "F1",
        "title": "Create and list tasks",
        "description": "Build task creation and listing.",
        "acceptance_criteria": ["API creates a task."],
        "dependencies": [],
        "qa_notes": ["Validate API and UI."],
        "deployment_notes": ["Deploy after sprint QA."],
        "delivery_order": 1,
        "status": "pending",
        "sprint_id": "sprint-01",
        "source_refs": ["F1"],
        "suggested_owner_agent": "fullstack-agent",
    }


def _register_run(run_dir: Path, monkeypatch) -> None:
    db_path = run_dir / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="pm@example.test",
        username="pm-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="PM",
        request_text="Plan",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
