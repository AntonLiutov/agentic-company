from agentic_company.console.web.db import CONSOLE_SCHEMA_VERSION, ConsoleRepository
from agentic_company.platform.artifact_registry import artifact_id_for, register_artifact
from agentic_company.platform.run_trace import ModelCallEvent, ToolCallEvent


def test_init_schema_records_schema_version(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()

    assert repo.schema_version() == CONSOLE_SCHEMA_VERSION


def test_sessions_and_private_project_isolation(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user_a = repo.create_user(email="a@example.test", username="alice", password="password-1")
    user_b = repo.create_user(email="b@example.test", username="bob", password="password-2")
    token = repo.create_session(user_a.id)
    project = repo.create_project(
        owner_user_id=user_a.id,
        name="Private app",
        request_text="Build something",
        mode="simple_prototype",
        complexity="simple",
    )

    assert repo.user_for_session(token) == user_a
    assert repo.get_project_for_user(project.id, user_a.id) is not None
    assert repo.get_project_for_user(project.id, user_b.id) is None


def test_public_demo_project_visible_to_other_users(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "demo"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv("PUBLIC_DEMO_RUN_DIR", str(run_dir))
    monkeypatch.setenv("PUBLIC_DEMO_PROJECT_NAME", "Demo Journey")
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(email="demo@example.test", username="demo", password="password-1")

    repo.seed_public_demo_from_env()

    project = repo.public_demo_project()
    assert project is not None
    assert project.name == "Demo Journey"
    assert repo.list_public_demo_projects() == [project]
    assert repo.get_project_for_user(project.id, user.id) is not None


def test_seed_public_demo_from_env_does_not_replace_other_showcases(tmp_path, monkeypatch):
    first_run_dir = tmp_path / "runs" / "first"
    second_run_dir = tmp_path / "runs" / "second"
    first_run_dir.mkdir(parents=True)
    second_run_dir.mkdir(parents=True)
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()

    monkeypatch.setenv("PUBLIC_DEMO_RUN_DIR", str(first_run_dir))
    monkeypatch.setenv("PUBLIC_DEMO_PROJECT_NAME", "First Showcase")
    repo.seed_public_demo_from_env()
    monkeypatch.setenv("PUBLIC_DEMO_RUN_DIR", str(second_run_dir))
    monkeypatch.setenv("PUBLIC_DEMO_PROJECT_NAME", "Second Showcase")
    repo.seed_public_demo_from_env()

    projects = repo.list_public_demo_projects()
    assert {project.name for project in projects} == {"First Showcase", "Second Showcase"}


def test_provider_key_storage_masks_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret")
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(email="key@example.test", username="keyuser", password="password-1")

    credential = repo.save_provider_secret(user.id, "openai", "sk-demo-secret-1234")

    assert credential.masked_value == "sk-demo...1234"
    assert credential.encrypted_value
    assert "sk-demo-secret-1234" not in credential.encrypted_value
    repo.delete_provider_secret(user.id, "openai")
    assert repo.get_provider_secret(user.id, "openai") is None


def test_artifact_metadata_upsert_and_filter(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(
        email="artifact@example.test",
        username="artifact",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Artifact Project",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report = run_dir / "08-qa-report-F1.md"
    report.write_text("# QA\n", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-artifacts",
        run_dir=run_dir,
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    record = register_artifact(
        run_dir,
        artifact_id=artifact_id_for(run.run_uid, "08-qa-report-F1.md"),
        relative_path="08-qa-report-F1.md",
        run_id=run.run_uid,
        owner_agent="qa-agent",
        visibility="qa_evidence",
        artifact_type="qa_report",
        label="QA report",
        source_tool="run_qa",
        external_refs=[{"system": "jira", "type": "work_item", "id": "ADL-1", "url": "https://x"}],
    )

    repo.upsert_artifact_record(run.id, record)

    loaded = repo.get_artifact_record(run.id, record.artifact_id)
    assert loaded is not None
    assert loaded.artifact_id == record.artifact_id
    assert loaded.relative_path == record.relative_path
    assert loaded.external_refs == record.external_refs
    qa_artifacts = repo.list_artifact_records(run.id, visibility="qa_evidence")
    assert [item.artifact_id for item in qa_artifacts] == [record.artifact_id]
    assert repo.list_artifact_records(run.id, visibility="business") == []


def test_trace_events_upsert_from_contract_records(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(
        email="trace@example.test",
        username="trace",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Trace Project",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "trace-run"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="trace-run",
        run_dir=run_dir,
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    repo.upsert_tool_call_event(
        run.id,
        ToolCallEvent(
            event_id="tool-call-1",
            run_id=run.run_uid,
            work_item_id="US-rooms",
            agent_id="team-lead-agent",
            tool_name="run_qa",
            tool_call_id="call-1",
            status="qa_passed",
            output_summary={"output_artifacts": [{"artifact_id": "art_trace"}]},
            artifact_ids=["art_trace"],
            duration_ms=5,
            created_at="2026-05-31T10:00:00Z",
        ),
    )
    repo.upsert_model_call_event(
        run.id,
        ModelCallEvent(
            event_id="model-call-1",
            run_id=run.run_uid,
            agent_id="qa-agent",
            provider="openai",
            model="gpt-5.5",
            purpose="codex_exec",
            prompt_ref="qa/prompt.md",
            status="qa_passed",
            created_at="2026-05-31T10:00:01Z",
        ),
    )
    repo.upsert_tool_call_event(
        run.id,
        ToolCallEvent(
            event_id="tool-call-1",
            run_id=run.run_uid,
            work_item_id="US-rooms",
            agent_id="team-lead-agent",
            tool_name="run_qa",
            tool_call_id="call-1",
            status="qa_passed",
            output_summary={"output_artifacts": [{"artifact_id": "art_trace"}]},
            artifact_ids=["art_trace"],
            duration_ms=5,
            created_at="2026-05-31T10:00:00Z",
        ),
    )

    tool_events = repo.list_tool_call_events(run.id, tool_name="run_qa")
    model_events = repo.list_model_call_events(run.id, agent_id="qa-agent")

    assert len(tool_events) == 1
    assert tool_events[0].artifact_ids == ["art_trace"]
    assert tool_events[0].duration_ms == 5
    assert len(model_events) == 1
    assert model_events[0].estimated_cost_usd is None


def test_activity_events_are_listed_chronologically(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(
        email="activity@example.test",
        username="activity",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Activity Project",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="activity-run",
        run_dir=tmp_path / "run",
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )
    with repo.connect() as conn:
        for event_id, message, created_at in [
            ("completed", "Business Analyst completed PLAN-01.", "2026-06-01T09:20:19Z"),
            ("first-note", "First user-facing note.", "2026-06-01T09:17:46Z"),
            ("second-note", "Second user-facing note.", "2026-06-01T09:17:51Z"),
        ]:
            conn.execute(
                """
                INSERT INTO activity_events (
                    run_id, event_id, work_item_id, owner_agent, agent_id, tool_name,
                    message, status, artifact_ids, visibility, created_at
                )
                VALUES (?, ?, 'PLAN-01', 'business-analyst-agent',
                    'business-analyst-agent', 'codex_agent_message',
                    ?, 'in_progress', '[]', 'user', ?)
                """,
                (run.id, event_id, message, created_at),
            )

    events = repo.list_activity_events(run.id, work_item_id="PLAN-01")

    assert [event.message for event in events] == [
        "First user-facing note.",
        "Second user-facing note.",
        "Business Analyst completed PLAN-01.",
    ]


def test_raw_log_events_are_append_only_and_filterable(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(email="logs@example.test", username="logs", password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Logs Project",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="logs-run",
        run_dir=tmp_path / "logs-run",
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )

    repo.append_raw_log_event(
        run.id,
        work_item_id="PLAN-01",
        sprint_id="planning",
        agent_id="business-analyst-agent",
        tool_name="codex_exec",
        tool_call_id="call-1",
        seq=1,
        level="info",
        stream="stdout",
        message="BA started",
    )
    repo.append_raw_log_event(
        run.id,
        work_item_id="PLAN-02",
        sprint_id="planning",
        agent_id="architect-agent",
        tool_name="codex_exec",
        tool_call_id="call-2",
        seq=1,
        level="info",
        stream="stdout",
        message="Architect started",
    )

    assert [event.message for event in repo.list_raw_log_events(run.id)] == [
        "BA started",
        "Architect started",
    ]
    scoped = repo.list_raw_log_events(run.id, work_item_id="PLAN-01")
    assert len(scoped) == 1
    assert scoped[0].agent_id == "business-analyst-agent"


def test_console_process_state_tracks_status_stop_and_env_keys(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(email="process@example.test", username="process", password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Process Project",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="process-run",
        run_dir=tmp_path / "process-run",
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )

    repo.upsert_console_process_state(
        run.id,
        process_name="codex_execution",
        status="running",
        thread_name="codex-thread",
        env_keys=["CODEX_API_KEY", "OPENAI_API_KEY"],
    )
    repo.request_console_process_stop(run.id, process_name="codex_execution")

    state = repo.get_console_process_state(run.id, "codex_execution")

    assert state is not None
    assert state.status == "stop_requested"
    assert state.thread_name == "codex-thread"
    assert state.env_keys == ["CODEX_API_KEY", "OPENAI_API_KEY"]
    assert state.stop_requested_at


def test_delete_private_project_removes_project_and_runs(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user = repo.create_user(email="owner@example.test", username="owner", password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Disposable",
        request_text="delete me",
        mode="simple_prototype",
        complexity="simple",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="delete-run",
        run_dir=tmp_path / "run",
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )

    assert repo.delete_private_project(project.id, user.id)

    assert repo.get_project_for_user(project.id, user.id) is None
    assert repo.runs_for_project(project.id, user.id) == []


def test_project_can_be_promoted_and_demoted_as_showcase(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    owner = repo.create_user(email="owner@example.test", username="owner", password="password-1")
    viewer = repo.create_user(email="viewer@example.test", username="viewer", password="password-1")
    project = repo.create_project(
        owner_user_id=owner.id,
        name="Showcase Candidate",
        request_text="promote me",
        mode="simple_prototype",
        complexity="simple",
    )

    assert repo.set_project_visibility(project.id, owner.id, "public_demo")
    promoted = repo.get_project_for_user(project.id, viewer.id)

    assert promoted is not None
    assert promoted.visibility == "public_demo"
    assert promoted.id in {item.id for item in repo.list_public_demo_projects()}
    assert project.id in {item.id for item in repo.list_projects_for_user(owner.id)}

    assert repo.set_project_visibility(project.id, owner.id, "private")
    assert repo.get_project_for_user(project.id, viewer.id) is None
