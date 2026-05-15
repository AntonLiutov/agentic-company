import json

from agentic_company.console.support import (
    PLANNING_ARTIFACTS,
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
    review_completed,
    run_codex_execution,
    saved_env_keys,
    start_azure_deployment,
    write_target_env,
)
from agentic_company.orchestration.graphs import (
    CONSOLE_DEPLOYMENT_NODE_ORDER,
    CONSOLE_EXECUTION_NODE_ORDER,
)


def test_console_run_writes_requirements_artifacts_and_runtime_events(tmp_path):
    requirements = load_sample_requirements()

    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert (run_dir / "00-requirements.md").exists()
    for filename, _, _ in PLANNING_ARTIFACTS:
        assert (run_dir / filename).exists()

    events = read_events(run_dir)
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_completed"
    assert "L0 Deterministic" in {event["runtime"] for event in events}
    assert "L6 Codex Agent" in {event["runtime"] for event in events}

    staffing = json.loads((run_dir / "03-staffing-decision.json").read_text(encoding="utf-8"))
    assert "Fullstack Agent" in staffing["selected_agents"]


def test_console_support_lists_and_loads_sample_requirements(tmp_path):
    requirements_dir = tmp_path / "examples" / "requirements"
    requirements_dir.mkdir(parents=True)
    (requirements_dir / "b-sample.md").write_text("Project name: B\n", encoding="utf-8")
    (requirements_dir / "a-sample.md").write_text("Project name: A\n", encoding="utf-8")

    samples = list_sample_requirements(tmp_path)

    assert [sample.name for sample in samples] == ["a-sample.md", "b-sample.md"]
    assert load_sample_requirements(tmp_path, "a-sample.md") == "Project name: A\n"


def test_artifact_groups_for_run_group_state_artifacts_by_agent(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for filename, _, _ in PLANNING_ARTIFACTS[:2]:
        (run_dir / filename).write_text("{}", encoding="utf-8")
    artifacts = [
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

    assert "Planning Agent" in grouped_paths
    assert groups[0][0] == "Documentation / Handoff Agent"
    assert grouped_paths["Documentation / Handoff Agent"] == ["handoff/release-report.html"]
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
                "project_archetype": "api-web-compose",
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

    overview = delivery_overview_for_run(run_dir)

    assert overview.run_id == "run"
    assert overview.stage == "handoff"
    assert overview.status == "handoff_ready"
    assert overview.project_archetype == "api-web-compose"
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
    assert overview.topology_summary == "API plus web service."
    assert [(target.label, target.url) for target in overview.deployment_targets] == [
        ("API", "https://api.example.test"),
        ("WEB", "https://web.example.test"),
    ]


def test_delivery_overview_falls_back_to_workflow_plan_before_execution(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "04-workflow-plan.json").write_text(
        json.dumps(
            {
                "feature_queue": [
                    {"id": "F1", "title": "Create tasks", "delivery_order": 1},
                    {"id": "F2", "title": "Complete tasks", "delivery_order": 2},
                ]
            }
        ),
        encoding="utf-8",
    )

    overview = delivery_overview_for_run(run_dir)

    assert overview.stage == "planning"
    assert overview.status == "planning_ready"
    assert overview.completed_feature_count == 0
    assert overview.total_feature_count == 2
    assert [feature.status for feature in overview.features] == ["pending", "pending"]


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

        def start(self, run_dir_arg, *, run_id, target_project_dir):
            calls["run_dir"] = run_dir_arg
            calls["run_id"] = run_id
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


def test_review_completed_accepts_feature_scoped_results(tmp_path):
    run_dir = tmp_path / "run"
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "results-F1.json").write_text('{"status": "passed"}\n', encoding="utf-8")

    assert review_completed(run_dir)


def test_console_support_writes_run_local_env_file(tmp_path):
    requirements = load_sample_requirements()
    run_dir = create_console_run(requirements, tmp_path / "runs")

    required = read_required_configuration(run_dir)
    env_path = write_target_env(
        run_dir,
        {
            "OPENAI_API_KEY": "sk-test",
            "DEFAULT_MODEL": "gpt-test",
        },
    )
    write_target_env(run_dir, {"OPENAI_API_KEY": "", "DEFAULT_MODEL": "gpt-next"})

    env_text = env_path.read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in required
    assert env_path == run_dir / "generated-project" / ".env"
    assert "OPENAI_API_KEY=sk-test" in env_text
    assert "DEFAULT_MODEL=gpt-next" in env_text
    assert saved_env_keys(run_dir) == ["DEFAULT_MODEL", "OPENAI_API_KEY"]


def test_console_support_requires_non_default_credentials_before_execution(tmp_path):
    requirements = load_sample_requirements()
    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert missing_required_env_keys(run_dir) == ["OPENAI_API_KEY"]

    env_path = ensure_required_env_defaults(run_dir)

    assert env_path == run_dir / "generated-project" / ".env"
    assert "DEFAULT_MODEL=gpt-4o-mini" in env_path.read_text(encoding="utf-8")
    assert missing_required_env_keys(run_dir) == ["OPENAI_API_KEY"]

    write_target_env(run_dir, {"OPENAI_API_KEY": "sk-test"})

    assert missing_required_env_keys(run_dir) == []


def test_console_support_validates_proposed_required_credentials(tmp_path):
    requirements = load_sample_requirements()
    run_dir = create_console_run(requirements, tmp_path / "runs")

    assert missing_required_env_keys(run_dir, {"OPENAI_API_KEY": ""}) == ["OPENAI_API_KEY"]
    assert missing_required_env_keys(run_dir, {"OPENAI_API_KEY": "sk-test"}) == []


def test_console_support_can_prefill_credentials_from_root_env(tmp_path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-root-test\nDEFAULT_MODEL=gpt-root\n",
        encoding="utf-8",
    )

    assert initial_env_value("OPENAI_API_KEY", tmp_path) == "sk-root-test"
    assert initial_env_value("DEFAULT_MODEL", tmp_path) == "gpt-root"
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
