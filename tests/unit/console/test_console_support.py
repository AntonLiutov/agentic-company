import json
from pathlib import Path

from agentic_company.console.support import (
    UPSTREAM_PLANNING_ARTIFACTS,
    TeamLeadStep,
    artifact_groups_for_run,
    clear_console_runs,
    codex_execution_running,
    console_status_label,
    create_console_run,
    delivery_overview_for_run,
    ensure_required_env_defaults,
    execution_completed,
    initial_env_value,
    list_sample_requirements,
    load_sample_requirements,
    missing_required_env_keys,
    read_events,
    read_required_configuration,
    request_codex_execution_stop,
    review_completed,
    run_codex_execution,
    saved_env_keys,
    start_azure_deployment,
    team_lead_step_rows,
    workflow_should_refresh,
    write_target_env,
)
from agentic_company.orchestration.graphs import (
    CONSOLE_DEPLOYMENT_NODE_ORDER,
    CONSOLE_EXECUTION_NODE_ORDER,
)


def test_console_run_writes_requirements_artifact_without_legacy_planning(tmp_path):
    requirements = load_sample_requirements()

    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert (run_dir / "00-requirements.md").exists()
    assert not (run_dir / "delivery" / "execution-request.json").exists()
    assert read_events(run_dir) == []


def test_console_support_lists_and_loads_sample_requirements(tmp_path):
    requirements_dir = tmp_path / "examples" / "requirements"
    requirements_dir.mkdir(parents=True)
    (requirements_dir / "b-sample.md").write_text("Project name: B\n", encoding="utf-8")
    (requirements_dir / "a-sample.md").write_text("Project name: A\n", encoding="utf-8")

    samples = list_sample_requirements(tmp_path)

    assert [sample.name for sample in samples] == ["a-sample.md", "b-sample.md"]
    assert load_sample_requirements(tmp_path, "a-sample.md") == "Project name: A\n"


def test_console_support_loads_multi_service_sample_by_default():
    requirements = load_sample_requirements()

    assert "I want a small web app for managing team tasks." in requirements
    assert "current Azure integration" in requirements
    assert "working app link and a short, business-facing demo report" in requirements


def test_artifact_groups_for_run_group_state_artifacts_by_agent(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for filename, _, _ in UPSTREAM_PLANNING_ARTIFACTS[:2]:
        path = run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    for filename in (
        "upstream-planning/architecture.md",
        "upstream-planning/architecture.json",
        "upstream-planning/architecture.mmd",
    ):
        path = run_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    artifacts = [
        {
            "path": "head/result.json",
            "kind": "internal",
            "owner_agent": "head-agent",
            "visibility": "developer",
        },
        {
            "path": "07-execution-summary-F1.md",
            "kind": "execution",
            "owner_agent": "fullstack-agent",
            "visibility": "user",
        },
        {
            "path": "qa/results-F1.json",
            "kind": "qa",
            "owner_agent": "qa-agent",
            "visibility": "user",
        },
        {
            "path": "qa/codex/F1/attempt-1/prompt.md",
            "kind": "qa",
            "owner_agent": "qa-agent",
            "visibility": "user",
        },
        {
            "path": "handoff/release-report.html",
            "kind": "handoff",
            "owner_agent": "handoff-codex-agent",
            "visibility": "user",
        },
        {
            "path": "team-lead/sprint-01-result.json",
            "kind": "internal",
            "owner_agent": "team-lead-agent",
            "visibility": "developer",
        },
    ]
    for artifact in artifacts:
        path = run_dir / artifact["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "artifact", encoding="utf-8")
    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"artifacts": artifacts}),
        encoding="utf-8",
    )

    groups = artifact_groups_for_run(run_dir)
    grouped_paths = {
        group_name: [artifact[0] for artifact in group_artifacts]
        for group_name, _, group_artifacts in groups
    }
    qa_labels = [
        artifact[1]
        for group_name, _, group_artifacts in groups
        if group_name == "QA Agent"
        for artifact in group_artifacts
    ]

    assert "Business Analyst" in grouped_paths
    assert "Head Agent" in grouped_paths
    assert "Architect" in grouped_paths
    assert groups[0][0] == "Documentation / Handoff Agent"
    assert grouped_paths["Business Analyst"] == [
        "upstream-planning/business-analysis.md",
        "upstream-planning/business-analysis.json",
    ]
    assert grouped_paths["Architect"] == [
        "upstream-planning/architecture.md",
        "upstream-planning/architecture.json",
        "upstream-planning/architecture.mmd",
    ]
    assert grouped_paths["Documentation / Handoff Agent"] == ["handoff/release-report.html"]
    assert grouped_paths["Team Lead Agent"] == ["team-lead/sprint-01-result.json"]
    assert grouped_paths["Head Agent"] == ["head/result.json"]
    assert grouped_paths["Fullstack Agent"] == ["07-execution-summary-F1.md"]
    assert grouped_paths["QA Agent"] == [
        "qa/results-F1.json",
        "qa/codex/F1/attempt-1/prompt.md",
    ]
    assert "F1 - QA results" in qa_labels
    assert "F1 - Codex prompt" in qa_labels


def test_delivery_overview_scales_feature_queue_and_deployment_targets(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    feature_queue = [
        {
            "id": f"F{index}",
            "title": f"Feature {index}",
            "delivery_order": index,
            "suggested_owner_agent": "fullstack-agent",
        }
        for index in range(1, 8)
    ]
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "handoff",
                "status": "handoff_ready",
                "active_feature_id": None,
                "feature_queue": feature_queue,
                "completed_feature_ids": [f"F{index}" for index in range(1, 8)],
                "feature_statuses": {f"F{index}": "qa_passed" for index in range(1, 8)},
                "feature_repair_attempts": {"F3": 2},
                "qa_status": "passed",
                "deployment_status": "deployed",
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    deployment_dir = run_dir / "deployment"
    deployment_dir.mkdir()
    (deployment_dir / "result.json").write_text(
        json.dumps(
            {
                "status": "deployed",
                "topology_summary": "API plus web service.",
                "deployment_targets": [
                    {
                        "service": "api",
                        "public_url": "https://api.example.test",
                    },
                    {
                        "service": "web",
                        "public_url": "https://web.example.test",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "handoff").mkdir()
    (run_dir / "handoff" / "release-report.html").write_text("<html></html>", encoding="utf-8")
    team_lead_dir = run_dir / "team-lead"
    team_lead_dir.mkdir()
    (team_lead_dir / "sprint-01-history.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "step": 1,
                        "tool": "assign_next_feature",
                        "target": "F1",
                        "reason": "Start sprint.",
                        "result_status": "team_lead_feature_selected",
                    },
                    {
                        "step": 2,
                        "tool": "run_fullstack",
                        "target": "F1",
                        "reason": "Implement feature.",
                        "result_status": "fullstack_feature_implemented",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.run_id == "run"
    assert overview.stage == "handoff"
    assert overview.status == "handoff_ready"
    assert overview.completed_feature_count == 7
    assert overview.total_feature_count == 7
    assert [feature.feature_id for feature in overview.features] == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
    ]
    assert overview.features[2].repair_attempts == 2
    assert overview.qa_status == "passed"
    assert overview.deployment_status == "deployed"
    assert overview.handoff_status == "ready"
    assert [step.tool for step in overview.team_lead_steps] == [
        "assign_next_feature",
        "run_fullstack",
    ]
    assert overview.team_lead_steps[1].reason == "Implement feature."
    assert overview.topology_summary == "API plus web service."
    assert [(target.label, target.url) for target in overview.deployment_targets] == [
        ("API", "https://api.example.test"),
        ("WEB", "https://web.example.test"),
    ]


def test_delivery_overview_marks_deployment_board_item_done_when_deployed(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    deployment_dir = run_dir / "deployment"
    deployment_dir.mkdir()
    (deployment_dir / "result.json").write_text(
        json.dumps({"status": "deployed"}),
        encoding="utf-8",
    )
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "head",
                "status": "head_delivery_completed",
                "active_feature_id": "F6",
                "deployment_status": "deployed",
                "work_board": {
                    "items": [
                        {
                            "item_id": "F1",
                            "title": "Feature",
                            "status": "qa_passed",
                            "lane": "done",
                            "owner_agent": "fullstack-agent",
                            "delivery_order": 1,
                        },
                        {
                            "item_id": "F6",
                            "title": "Deploy",
                            "status": "pending",
                            "lane": "todo",
                            "active": True,
                            "owner_agent": "deployment-agent",
                            "delivery_order": 2,
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)
    deployed = next(feature for feature in overview.features if feature.feature_id == "F6")

    assert deployed.status == "deployed"
    assert deployed.lane == "done"
    assert not deployed.active
    assert overview.completed_feature_count == 2


def test_delivery_overview_tolerates_temporarily_locked_state_file(
    tmp_path,
    monkeypatch,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state_path = run_dir / ".delivery-state.json"
    state_path.write_text(json.dumps({"run_id": "run", "stage": "qa"}), encoding="utf-8")
    original_read_text = Path.read_text

    def fake_read_text(path, *args, **kwargs):
        if path == state_path:
            raise PermissionError("state file is locked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    monkeypatch.setattr("agentic_company.console.support.time.sleep", lambda _: None)

    overview = delivery_overview_for_run(run_dir)

    assert overview.run_id == "run"
    assert overview.status == "planning_ready"


def test_delivery_overview_stage_prefers_active_worker_event(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "business_analysis",
                "status": "business_analysis_completed",
                "feature_queue": [],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-11T01:00:00",
                        "run_id": "run",
                        "agent_id": "delivery-graph",
                        "event": "delivery_graph_node_started",
                        "data": {"node": "head", "stage": "initialized"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-11T01:00:01",
                        "run_id": "run",
                        "agent_id": "head-agent",
                        "event": "head_worker_started",
                        "data": {"node": "project_management"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.stage == "project_management"


def test_delivery_overview_stage_falls_back_after_worker_completed(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "project_management",
                "status": "project_management_completed",
                "feature_queue": [],
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-11T01:00:00",
                        "run_id": "run",
                        "agent_id": "head-agent",
                        "event": "head_worker_started",
                        "data": {"node": "project_management"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-11T01:00:01",
                        "run_id": "run",
                        "agent_id": "head-agent",
                        "event": "head_worker_completed",
                        "data": {"node": "project_management"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.stage == "project_management"


def test_team_lead_step_rows_show_full_decision_history():
    rows = team_lead_step_rows(
        [
            TeamLeadStep(
                step=index,
                tool="run_fullstack",
                target=f"F{index}",
                reason=f"Decision {index}",
                status="fullstack_feature_implemented",
            )
            for index in range(1, 19)
        ]
    )

    assert len(rows) == 18
    assert rows[0]["Step"] == 1
    assert rows[-1]["Step"] == 18
    assert rows[0]["Tool"] == "Fullstack"


def test_delivery_overview_uses_project_manager_candidate_queue_before_delivery(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    pm_dir = run_dir / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "candidate-feature-queue.json").write_text(
        json.dumps(
            [
                {"id": "F1", "title": "Create tasks", "delivery_order": 1},
                {"id": "F2", "title": "Complete tasks", "delivery_order": 2},
            ]
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.stage == "project_management"
    assert overview.status == "planning_ready"
    assert overview.completed_feature_count == 0
    assert overview.total_feature_count == 2
    assert [feature.status for feature in overview.features] == ["pending", "pending"]


def test_delivery_overview_prefers_runtime_work_board(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "team_lead",
                "status": "team_lead_feature_selected",
                "active_feature_id": "F1",
                "feature_repair_attempts": {"F1": 1},
                "work_board": {
                    "sprint_id": "sprint-01",
                    "active_item_id": "F1",
                    "status_counts": {"in_progress": 1},
                    "items": [
                        {
                            "item_id": "F1",
                            "title": "Build task flow",
                            "sprint_id": "sprint-01",
                            "status": "in_progress",
                            "lane": "doing",
                            "owner_agent": "fullstack-agent",
                            "assigned_agent": "fullstack-agent",
                            "delivery_order": 1,
                            "story_points": 3,
                            "active": True,
                            "artifact_refs": ["codex/F1/summary.md"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert len(overview.features) == 1
    feature = overview.features[0]
    assert feature.feature_id == "F1"
    assert feature.status == "in_progress"
    assert feature.lane == "doing"
    assert feature.assigned_agent == "fullstack-agent"
    assert feature.story_points == 3
    assert feature.artifact_count == 1
    assert feature.repair_attempts == 1
    assert overview.current_work is not None
    assert overview.current_work.feature_id == "F1"
    assert overview.current_work.status == "in_progress"
    assert overview.current_work.assigned_agent == "fullstack-agent"


def test_delivery_overview_corrects_completed_handoff_and_stale_assigned_agents(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "handoff" / "sprints" / "sprint-01").mkdir(parents=True)
    (run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html").write_text(
        "<html>ready</html>",
        encoding="utf-8",
    )
    stale_assigned = "documentation-handoff-agent"
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "head",
                "status": "head_delivery_completed",
                "deployment_status": "deployed",
                "work_board": {
                    "sprint_id": "sprint-01",
                    "active_item_id": "DEPLOY",
                    "items": [
                        {
                            "item_id": "F1",
                            "title": "Browser Task Tracker Demo",
                            "sprint_id": "sprint-01",
                            "status": "qa_passed",
                            "lane": "done",
                            "owner_agent": "fullstack-agent",
                            "assigned_agent": stale_assigned,
                            "delivery_order": 1,
                        },
                        {
                            "item_id": "DEPLOY",
                            "title": "Azure Demo Deployment",
                            "sprint_id": "sprint-01",
                            "status": "deployed",
                            "lane": "done",
                            "owner_agent": "deployment-agent",
                            "assigned_agent": stale_assigned,
                            "delivery_order": 2,
                        },
                        {
                            "item_id": "HANDOFF",
                            "title": "Completion Report",
                            "sprint_id": "sprint-01",
                            "status": "pending",
                            "lane": "todo",
                            "owner_agent": "documentation-handoff-agent",
                            "assigned_agent": stale_assigned,
                            "delivery_order": 3,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.handoff_status == "ready"
    assert overview.completed_feature_count == 3
    assert overview.total_feature_count == 3
    statuses = {feature.feature_id: feature.status for feature in overview.features}
    assert statuses == {
        "F1": "qa_passed",
        "DEPLOY": "deployed",
        "HANDOFF": "handoff_ready",
    }
    assert {feature.assigned_agent for feature in overview.features} == {""}


def test_delivery_overview_closes_stale_review_items_after_successful_final_delivery(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "handoff" / "sprints" / "sprint-01").mkdir(parents=True)
    (run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html").write_text(
        "<html>ready</html>",
        encoding="utf-8",
    )
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "head",
                "status": "head_delivery_completed",
                "qa_status": "passed",
                "deployment_status": "deployed",
                "active_feature_id": "F2",
                "blockers": [],
                "work_board": {
                    "sprint_id": "sprint-01",
                    "active_item_id": "F2",
                    "items": [
                        {
                            "item_id": "F1",
                            "title": "Completed feature",
                            "sprint_id": "sprint-01",
                            "status": "qa_passed",
                            "lane": "done",
                            "owner_agent": "fullstack-agent",
                            "delivery_order": 1,
                        },
                        {
                            "item_id": "F2",
                            "title": "Stale review feature",
                            "sprint_id": "sprint-01",
                            "status": "implemented",
                            "lane": "review",
                            "active": True,
                            "owner_agent": "fullstack-agent",
                            "assigned_agent": "documentation-handoff-agent",
                            "delivery_order": 2,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)
    stale = next(feature for feature in overview.features if feature.feature_id == "F2")

    assert overview.completed_feature_count == 2
    assert stale.status == "qa_passed"
    assert stale.lane == "done"
    assert stale.active is False
    assert stale.assigned_agent == ""


def test_delivery_overview_does_not_count_previous_sprint_handoff_for_current_sprint(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "handoff" / "sprints" / "sprint-01").mkdir(parents=True)
    (run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html").write_text(
        "<html>sprint 1 ready</html>",
        encoding="utf-8",
    )
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "stage": "team_lead",
                "status": "deployment_deployed",
                "team_lead_sprint_id": "sprint-02",
                "deployment_status": "deployed",
                "work_board": {
                    "sprint_id": "sprint-02",
                    "items": [
                        {
                            "item_id": "DEPLOY",
                            "title": "Azure Dev Deployment",
                            "sprint_id": "sprint-02",
                            "status": "pending",
                            "lane": "todo",
                            "owner_agent": "deployment-agent",
                            "delivery_order": 1,
                        },
                        {
                            "item_id": "QA-LIVE",
                            "title": "Live Azure Flow And Design Checks",
                            "sprint_id": "sprint-02",
                            "status": "pending",
                            "lane": "todo",
                            "owner_agent": "qa-agent",
                            "delivery_order": 2,
                        },
                        {
                            "item_id": "HANDOFF",
                            "title": "Business-Facing Demo Report",
                            "sprint_id": "sprint-02",
                            "status": "pending",
                            "lane": "todo",
                            "owner_agent": "documentation-handoff-agent",
                            "delivery_order": 3,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.handoff_status == ""
    assert overview.completed_feature_count == 1
    assert {feature.feature_id: feature.status for feature in overview.features} == {
        "DEPLOY": "deployed",
        "QA-LIVE": "pending",
        "HANDOFF": "pending",
    }


def test_delivery_overview_reads_all_team_lead_histories(tmp_path):
    run_dir = tmp_path / "run"
    team_lead_dir = run_dir / "team-lead"
    team_lead_dir.mkdir(parents=True)
    for sprint_id, target in [("sprint-01", "F1"), ("sprint-02", "F2")]:
        (team_lead_dir / f"{sprint_id}-history.json").write_text(
            json.dumps(
                {
                    "steps": [
                        {
                            "step": 1,
                            "tool": "run_fullstack",
                            "target": target,
                            "reason": f"Implement {target}.",
                            "result_status": "fullstack_feature_implemented",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    overview = delivery_overview_for_run(run_dir)

    assert [step.step for step in overview.team_lead_steps] == [1, 2]
    assert [step.target for step in overview.team_lead_steps] == ["F1", "F2"]


def test_delivery_overview_recovers_team_lead_history_from_events_when_history_is_truncated(
    tmp_path,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "agent_id": "team-lead-agent",
                        "event": "team_lead_decision",
                        "data": {
                            "decision": {
                                "tool": "inspect_sprint_status",
                                "target": "sprint-01",
                                "reason": "Inspect first pass.",
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "agent_id": "team-lead-agent",
                        "event": "team_lead_tool_completed",
                        "data": {"status": "running"},
                    }
                ),
                json.dumps(
                    {
                        "agent_id": "team-lead-agent",
                        "event": "team_lead_decision",
                        "data": {
                            "decision": {
                                "tool": "run_fullstack",
                                "target": "US-05",
                                "reason": "Continue sprint.",
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "agent_id": "team-lead-agent",
                        "event": "team_lead_tool_completed",
                        "data": {"status": "fullstack_feature_implemented"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    team_lead_dir = run_dir / "team-lead"
    team_lead_dir.mkdir()
    (team_lead_dir / "sprint-01-history.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "tool": "run_fullstack",
                        "target": "US-05",
                        "reason": "Continue sprint.",
                        "result_status": "fullstack_feature_implemented",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert [step.tool for step in overview.team_lead_steps] == [
        "inspect_sprint_status",
        "run_fullstack",
    ]
    assert [step.status for step in overview.team_lead_steps] == [
        "running",
        "fullstack_feature_implemented",
    ]


def test_delivery_overview_current_work_maps_deployment_stage_to_deploy_item(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "run",
                "run_dir": str(run_dir),
                "stage": "team_lead",
                "status": "team_lead_sprint_started",
                "agent_call_correlation_id": "DEPLOY",
                "agent_execution_agent_id": "deployment-agent",
                "work_board": {
                    "items": [
                        {
                            "item_id": "DEPLOY",
                            "title": "Azure Dev Deployment",
                            "status": "pending",
                            "lane": "todo",
                            "owner_agent": "deployment-agent",
                            "assigned_agent": "deployment-agent",
                            "delivery_order": 1,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "agent_id": "team-lead-agent",
                "event": "team_lead_worker_started",
                "data": {"node": "deployment", "active_feature_id": None},
            }
        )
        + "\n"
        + json.dumps(
            {
                "agent_id": "deployment-agent",
                "event": "deployment_started",
                "data": {"release_strategy": "release_batch"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.current_work is not None
    assert overview.current_work.stage == "deployment"
    assert overview.current_work.feature_id == "DEPLOY"
    assert overview.current_work.title == "Azure Dev Deployment"
    assert overview.current_work.status == "in_progress"
    assert overview.current_work.lane == "doing"
    assert overview.current_work.assigned_agent == "deployment-agent"
    assert len(overview.features) == 1
    assert overview.features[0].feature_id == "DEPLOY"
    assert overview.features[0].active is True
    assert overview.features[0].status == "in_progress"
    assert overview.features[0].lane == "doing"
    assert overview.features[0].assigned_agent == "deployment-agent"


def test_console_status_label_keeps_qa_uppercase():
    assert console_status_label("qa") == "QA"
    assert console_status_label("qa_passed") == "QA Passed"
    assert console_status_label("feature_queue_qa_completed") == "Feature Queue QA Completed"


def test_console_support_runs_codex_execution_through_graph_runtime(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *, node_order):
            calls["node_order"] = node_order

        def start(self, run_dir_arg, *, run_id, requirements_path, target_project_dir):
            calls["run_dir"] = run_dir_arg
            calls["run_id"] = run_id
            calls["requirements_path"] = requirements_path
            calls["target_project_dir"] = target_project_dir
            (run_dir_arg / "07-execution-summary.md").write_text(
                "# Execution Summary\n\nStatus: codex completed\n",
                encoding="utf-8",
            )
            return {"status": "qa_passed"}

    monkeypatch.setattr("agentic_company.console.support.DeliveryGraphRuntime", FakeRuntime)

    summary = run_codex_execution(run_dir)

    assert calls["node_order"] == CONSOLE_EXECUTION_NODE_ORDER
    assert calls["run_dir"] == run_dir
    assert calls["run_id"] == "run"
    assert calls["requirements_path"] == run_dir / "00-requirements.md"
    assert calls["target_project_dir"] == run_dir / "generated-project"
    assert "Status: codex completed" in summary


def test_console_support_starts_deployment_through_graph_runtime(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: dict[str, object] = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            calls["thread_name"] = name
            self.name = name
            self.ident = 123
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

        def is_alive(self):
            return False

    class FakeRuntime:
        def __init__(self, *, node_order):
            calls["node_order"] = node_order

        def start(self, run_dir_arg, *, run_id, target_project_dir):
            calls["run_dir"] = run_dir_arg
            calls["run_id"] = run_id
            calls["target_project_dir"] = target_project_dir
            (run_dir_arg / "13-deployment-summary.md").write_text(
                "# Deployment Summary\n\nStatus: deployed\n",
                encoding="utf-8",
            )
            return {"status": "completed"}

    monkeypatch.setattr("agentic_company.console.support.threading.Thread", FakeThread)
    monkeypatch.setattr("agentic_company.console.support.DeliveryGraphRuntime", FakeRuntime)

    thread_id = start_azure_deployment(run_dir)

    assert thread_id == 123
    assert calls["node_order"] == CONSOLE_DEPLOYMENT_NODE_ORDER
    assert calls["run_dir"] == run_dir
    assert calls["run_id"] == "run"
    assert calls["target_project_dir"] == run_dir / "generated-project"


def test_clear_console_runs_only_removes_console_prefixed_directories(tmp_path):
    runs_root = tmp_path / "runs"
    (runs_root / "console-123").mkdir(parents=True)
    (runs_root / "smoke-123").mkdir(parents=True)
    (runs_root / "demo-123").mkdir(parents=True)

    result = clear_console_runs(runs_root)

    assert result.deleted == 1
    assert result.skipped == []
    assert not (runs_root / "console-123").exists()
    assert (runs_root / "smoke-123").exists()
    assert (runs_root / "demo-123").exists()


def test_clear_console_runs_skips_locked_directories(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    locked = runs_root / "console-locked"
    removable = runs_root / "console-removable"
    locked.mkdir(parents=True)
    removable.mkdir()

    def fake_rmtree(path):
        if path == locked:
            raise PermissionError("folder is locked")
        path.rmdir()

    monkeypatch.setattr("agentic_company.console.support.shutil.rmtree", fake_rmtree)

    result = clear_console_runs(runs_root)

    assert result.deleted == 1
    assert len(result.skipped) == 1
    assert "console-locked" in result.skipped[0]
    assert locked.exists()
    assert not removable.exists()


def test_execution_completed_keeps_failed_codex_runs_retryable(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    summary = run_dir / "07-execution-summary.md"

    assert not execution_completed(run_dir)

    summary.write_text("# Execution Summary\n\nStatus: codex failed\n", encoding="utf-8")

    assert not execution_completed(run_dir)

    summary.write_text("# Execution Summary\n\nStatus: codex completed\n", encoding="utf-8")

    assert execution_completed(run_dir)


def test_execution_completed_accepts_feature_scoped_summaries_and_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    (run_dir / "07-execution-summary-F1.md").write_text(
        "# Execution Summary\n\nStatus: codex completed\n",
        encoding="utf-8",
    )

    assert execution_completed(run_dir)

    (run_dir / "07-execution-summary-F2.md").write_text(
        "# Execution Summary\n\nStatus: codex failed\n",
        encoding="utf-8",
    )

    assert not execution_completed(run_dir)

    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "feature_queue_qa_completed_downstream_paused"}),
        encoding="utf-8",
    )

    assert execution_completed(run_dir)


def test_codex_execution_running_detects_feature_scoped_log(tmp_path):
    run_dir = tmp_path / "run"
    feature_log = run_dir / "codex" / "F1" / "execution.log"
    feature_log.parent.mkdir(parents=True)
    feature_log.write_text("status=running\n", encoding="utf-8")

    assert codex_execution_running(run_dir)


def test_request_codex_execution_stop_marks_run_not_running(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")

    request_codex_execution_stop(run_dir)

    assert not codex_execution_running(run_dir)
    assert (run_dir / ".stop-requested").exists()


def test_codex_execution_running_detects_execution_scoped_log(tmp_path):
    run_dir = tmp_path / "run"
    execution_log = run_dir / "codex" / "F1" / "exec-run-fullstack-f1" / "execution.log"
    execution_log.parent.mkdir(parents=True)
    execution_log.write_text("status=running\n", encoding="utf-8")

    assert codex_execution_running(run_dir)


def test_review_completed_accepts_feature_scoped_results(tmp_path):
    run_dir = tmp_path / "run"
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "results-F1.json").write_text('{"status": "passed"}\n', encoding="utf-8")

    assert review_completed(run_dir)


def test_console_support_writes_run_local_env_file(tmp_path):
    requirements = load_sample_requirements(filename="multi-service-task-tracker.md")
    run_dir = create_console_run(requirements, tmp_path / "runs")

    required = read_required_configuration(run_dir)
    env_path = write_target_env(
        run_dir,
        {
            "OPENAI_API_KEY": "sk-test",
            "AGENT_LLM_MODEL": "gpt-test",
        },
    )
    write_target_env(run_dir, {"OPENAI_API_KEY": "", "AGENT_LLM_MODEL": "gpt-next"})

    env_text = env_path.read_text(encoding="utf-8")

    assert required == []
    assert env_path == run_dir / "generated-project" / ".env"
    assert "OPENAI_API_KEY=sk-test" in env_text
    assert "AGENT_LLM_MODEL=gpt-next" in env_text
    assert saved_env_keys(run_dir) == ["AGENT_LLM_MODEL", "OPENAI_API_KEY"]


def test_console_support_requires_non_default_credentials_before_execution(tmp_path):
    requirements = load_sample_requirements(filename="multi-service-task-tracker.md")
    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert missing_required_env_keys(run_dir) == []

    env_path = ensure_required_env_defaults(run_dir)

    assert env_path == run_dir / "generated-project" / ".env"
    assert not env_path.exists()
    assert missing_required_env_keys(run_dir) == []

    write_target_env(run_dir, {"OPENAI_API_KEY": "sk-test"})

    assert missing_required_env_keys(run_dir) == []


def test_console_support_validates_proposed_required_credentials(tmp_path):
    requirements = load_sample_requirements(filename="multi-service-task-tracker.md")
    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert missing_required_env_keys(run_dir, {"OPENAI_API_KEY": ""}) == []
    assert missing_required_env_keys(run_dir, {"OPENAI_API_KEY": "sk-test"}) == []


def test_console_support_can_prefill_credentials_from_root_env(tmp_path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-root-test\nAGENT_LLM_MODEL=gpt-root\n",
        encoding="utf-8",
    )

    assert initial_env_value("OPENAI_API_KEY", tmp_path) == "sk-root-test"
    assert initial_env_value("AGENT_LLM_MODEL", tmp_path) == "gpt-root"
    assert initial_env_value("UNKNOWN_KEY", tmp_path) == ""


def test_codex_execution_running_is_false_after_failed_summary(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\npid=999999\n", encoding="utf-8")
    (run_dir / "07-execution-summary.md").write_text(
        "# Execution Summary\n\nStatus: codex failed\n",
        encoding="utf-8",
    )

    assert not codex_execution_running(run_dir)


def test_codex_execution_running_stays_true_until_graph_reaches_terminal_state(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")
    (run_dir / "07-execution-summary.md").write_text(
        "# Execution Summary\n\nStatus: codex completed\n",
        encoding="utf-8",
    )

    assert execution_completed(run_dir)
    assert not review_completed(run_dir)
    assert codex_execution_running(run_dir)

    (run_dir / "qa").mkdir()
    (run_dir / "qa" / "results.json").write_text('{"status": "passed"}\n', encoding="utf-8")

    assert review_completed(run_dir)
    assert codex_execution_running(run_dir)

    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "feature_queue_qa_completed_downstream_paused"}),
        encoding="utf-8",
    )

    assert not codex_execution_running(run_dir)


def test_codex_execution_running_continues_after_business_analysis_until_pm(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")
    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "business_analysis_completed"}),
        encoding="utf-8",
    )

    assert codex_execution_running(run_dir)


def test_codex_execution_running_continues_after_architecture_until_pm(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")
    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "architecture_completed"}),
        encoding="utf-8",
    )

    assert codex_execution_running(run_dir)


def test_codex_execution_running_continues_after_project_management_until_team_lead(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")
    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "project_management_completed"}),
        encoding="utf-8",
    )

    assert codex_execution_running(run_dir)


def test_codex_execution_running_continues_after_team_lead_handoff_until_head_terminal(
    tmp_path,
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".codex-execution.status").write_text("running\n", encoding="utf-8")
    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "team_lead_sprint_handoff_ready"}),
        encoding="utf-8",
    )

    assert codex_execution_running(run_dir)

    (run_dir / ".delivery-state.json").write_text(
        json.dumps({"status": "head_delivery_completed"}),
        encoding="utf-8",
    )

    assert not codex_execution_running(run_dir)


def test_workflow_refresh_continues_after_team_lead_until_head_completes(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "execution_started"}),
                json.dumps({"event": "handoff_completed"}),
                json.dumps({"event": "team_lead_sprint_completed"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert workflow_should_refresh(run_dir, execution_is_running=False)

    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "head_agent_completed"}) + "\n")

    assert not workflow_should_refresh(run_dir, execution_is_running=False)
