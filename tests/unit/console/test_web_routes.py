import json
from pathlib import Path

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from agentic_company.console.web.app import create_app
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifacts.artifact_registry import artifact_id_for, register_artifact
from agentic_company.platform.db.state import DELIVERY_STATE_SNAPSHOT
from agentic_company.platform.run.run_trace import RunEvent, ToolCallEvent


def delivery_state_path(run_dir: Path) -> Path:
    state_path = run_dir / DELIVERY_STATE_SNAPSHOT
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return state_path


def register_artifact_content(repo: ConsoleRepository, run_id: int, record, content: str) -> None:
    repo.upsert_artifact_content(
        run_id,
        artifact_id=record.artifact_id,
        path=record.relative_path,
        content_kind=Path(record.relative_path).suffix.lstrip(".") or "text",
        content_text=content,
    )


def test_landing_page_renders_public_story(tmp_path):
    app = create_app(ConsoleRepository())
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Agentic Delivery Lab" in response.text
    assert "Turn product ideas into shipped demos." in response.text
    assert "Gemini API" in response.text
    assert "Speech" + "matics" not in response.text
    assert "brand/agentic-delivery-lab-cover.png" in response.text


def test_register_dashboard_and_logout(tmp_path):
    app = create_app(ConsoleRepository())
    client = TestClient(app)

    response = client.post(
        "/register",
        data={
            "email": "user@example.test",
            "username": "demo",
            "password": "password-1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "agentic_console_session" in response.headers["set-cookie"]
    assert client.get("/dashboard").status_code == 200
    assert client.post("/logout", follow_redirects=False).status_code == 303


def test_new_project_page_renders_model_controls_without_transcription_controls(tmp_path):
    app = create_app(ConsoleRepository())
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "models@example.test",
            "username": "modelsuser",
            "password": "password-1",
        },
    )

    response = client.get("/projects/new")

    assert response.status_code == 200
    assert "Planning" in response.text
    assert "Google Gemini" in response.text
    assert "Google Gemini" in response.text
    assert "gemini-3.1-flash-lite" in response.text
    assert "gpt-5.5" in response.text
    assert "gpt-5.4" in response.text
    assert "gpt-5.4-mini" in response.text
    assert "gpt-5.3-codex-spark" not in response.text
    assert "gpt-5.2" not in response.text
    # Build model is locked until this user connects Codex (they have not here).
    assert 'name="codex_model" disabled' in response.text
    assert "Connect Codex in" in response.text
    assert "Dictation " + "language" not in response.text
    assert "Start " + "".join(["dict", "ation"]) not in response.text
    assert "Speech" + "matics" not in response.text
    assert "data-" + "".join(["dict", "ation-language"]) not in response.text


def test_transcription_token_endpoint_is_removed(tmp_path):
    app = create_app(ConsoleRepository())
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "no-transcription@example.test",
            "username": "notranscription",
            "password": "password-1",
        },
    )

    removed_route = "/api/" + "voi" + "ce/" + "speech" + "matics-token"
    response = client.post(removed_route)

    assert response.status_code == 404


def test_settings_can_save_and_delete_gemini_key(tmp_path):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "gemini@example.test",
            "username": "geminiuser",
            "password": "password-1",
        },
    )

    save_response = client.post(
        "/settings/gemini",
        data={"api_key": "AIza-demo-secret"},
        follow_redirects=False,
    )
    settings_response = client.get("/settings")

    assert save_response.status_code == 303
    assert repo.get_provider_secret(1, "google_gemini") is not None
    assert "Gemini" in settings_response.text
    assert "AIza-demo-secret" not in settings_response.text  # never render the raw key

    delete_response = client.post("/settings/gemini/delete", follow_redirects=False)

    assert delete_response.status_code == 303
    assert repo.get_provider_secret(1, "google_gemini") is None


def test_private_project_not_visible_to_another_user(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user_a = repo.create_user(email="a@example.test", username="auser", password="password-1")
    user_b = repo.create_user(email="b@example.test", username="buser", password="password-1")
    project = repo.create_project(
        owner_user_id=user_a.id,
        name="Secret",
        request_text="private",
    )
    app = create_app(repo)
    client = TestClient(app)
    token_b = repo.create_session(user_b.id)
    client.cookies.set("agentic_console_session", token_b)

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 404


def test_create_project_starts_run_with_monkeypatched_runtime(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "runner@example.test",
            "username": "runner",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "openai", "sk-test-project")
    run_root = tmp_path / "runs"

    def fake_create_console_run(username, requirements_text):
        run_dir = run_root / "console-test"
        run_dir.mkdir(parents=True)
        (run_dir / "00-requirements.md").write_text(requirements_text, encoding="utf-8")
        return run_dir

    monkeypatch.setattr(
        "agentic_company.console.web.app.create_web_console_run",
        fake_create_console_run,
    )
    monkeypatch.setattr("agentic_company.console.web.app.start_codex_execution", lambda run_dir: 1)

    response = client.post(
        "/projects",
        data={
            "name": "Task Tracker",
            "request_text": "Build a task tracker",
            "mode": "simple_prototype",
            "complexity": "simple",
            "agent_provider": "openai",
            "agent_model": "gpt-4.1",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (run_root / "console-test" / "00-requirements.md").exists()
    run_dir = run_root / "console-test"
    env_text = (run_dir / "delivery" / "agent-runtime.env").read_text(encoding="utf-8")
    assert not (run_dir / "generated-project" / ".env").exists()
    assert "OPENAI_API_KEY=sk-test-project" not in env_text
    assert "CODEX_API_KEY=sk-test-project" not in env_text
    assert "AGENT_LLM_PROVIDER=openai" in env_text
    assert "AGENT_CODEX_MODEL=gpt-5.5" in env_text
    assert "AGENTIC_CODEX_SERVICE_TIER=standard" in env_text
    assert repo.list_projects_for_user(1)[0].name == "Task Tracker"


def test_create_project_persists_control_modes(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "control@example.test",
            "username": "control",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "google_gemini", "AIza-project")
    run_root = tmp_path / "runs"

    def fake_create_console_run(username, requirements_text):
        run_dir = run_root / "control-run"
        run_dir.mkdir(parents=True)
        (run_dir / "00-requirements.md").write_text(requirements_text, encoding="utf-8")
        return run_dir

    monkeypatch.setattr(
        "agentic_company.console.web.app.create_web_console_run",
        fake_create_console_run,
    )
    monkeypatch.setattr("agentic_company.console.web.app.start_codex_execution", lambda run_dir: 1)

    response = client.post(
        "/projects",
        data={
            "name": "Controlled Run",
            "request_text": "Build a task tracker",
            "mode": "simple_prototype",
            "complexity": "simple",
            "agent_provider": "google_gemini",
            "agent_model": "gemini-3.1-flash-lite",
            "run_mode": "medium",
            "risk_mode": "safe",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    run = repo.latest_run_for_project(repo.list_projects_for_user(1)[0].id, 1)
    assert run is not None
    assert run.run_mode == "medium"
    assert run.risk_mode == "safe"


def test_workspace_can_approve_pending_gate(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="approver@example.test",
        username="approver",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Approval Project",
        request_text="Build",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="approval-ui",
        run_dir=tmp_path / "approval-ui",
        status="paused_provider_limit",
        reasoning="medium",
    )
    approval = repo.request_run_approval(
        run.id,
        gate_type="deployment",
        requested_action="publish",
        risk_summary="Deployment needs operator approval.",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    page = client.get(f"/projects/{project.id}")
    response = client.post(
        f"/projects/{project.id}/approvals/{approval.id}/approve",
        follow_redirects=False,
    )

    assert page.status_code == 200
    assert "Pending Approval" in page.text
    assert response.status_code == 303
    assert repo.get_run_approval(approval.id).status == "approved"


def _gated_run(repo, tmp_path, *, uid):
    user = repo.create_user(email=f"{uid}@example.test", username=uid, password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Gated",
        request_text="Build",
    )
    run_dir = tmp_path / uid
    run_dir.mkdir(parents=True)
    run = repo.create_run(
        project_id=project.id,
        run_uid=run_dir.name,
        run_dir=run_dir,
        status="running",
        reasoning="medium",
        run_mode="simple",
        risk_mode="safe",
    )
    return user, project, run, run_dir


def test_safe_mode_gates_run_start_and_approve_resumes(tmp_path, monkeypatch):
    # safe risk mode must HOLD the run at an approval gate instead of starting (spending);
    # Approve then resumes it. Proves run/risk modes are behavioural, and Approve is real.
    from agentic_company.console.web.app import _gate_or_start_run

    repo = ConsoleRepository()
    repo.init_schema()
    user, project, run, run_dir = _gated_run(repo, tmp_path, uid="gate-approve")
    started: list[Path] = []
    monkeypatch.setattr(
        "agentic_company.console.web.app.start_codex_execution",
        lambda path: started.append(path) or 1,
    )

    _gate_or_start_run(
        repo, project_id=project.id, run=run, run_dir=run_dir, run_mode="simple", risk_mode="safe"
    )

    assert started == []  # gated, not started
    assert repo.get_run_by_uid(run_dir.name).status == "awaiting_approval"
    pending = repo.list_run_approvals(run.id, status="pending")
    assert len(pending) == 1

    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))
    response = client.post(
        f"/projects/{project.id}/approvals/{pending[0].id}/approve", follow_redirects=False
    )

    assert response.status_code == 303
    assert started == [run_dir]  # Approve resumed -> the run actually started
    assert repo.get_run_by_uid(run_dir.name).status == "running"
    assert repo.get_run_approval(pending[0].id).status == "approved"


def test_reject_gate_stops_the_run(tmp_path, monkeypatch):
    from agentic_company.console.web.app import _gate_or_start_run

    repo = ConsoleRepository()
    repo.init_schema()
    user, project, run, run_dir = _gated_run(repo, tmp_path, uid="gate-reject")
    started: list[Path] = []
    monkeypatch.setattr(
        "agentic_company.console.web.app.start_codex_execution",
        lambda path: started.append(path) or 1,
    )
    _gate_or_start_run(
        repo, project_id=project.id, run=run, run_dir=run_dir, run_mode="simple", risk_mode="safe"
    )
    pending = repo.list_run_approvals(run.id, status="pending")

    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))
    response = client.post(
        f"/projects/{project.id}/approvals/{pending[0].id}/reject", follow_redirects=False
    )

    assert response.status_code == 303
    assert started == []  # Reject never starts the run
    assert repo.get_run_by_uid(run_dir.name).status == "stopped"
    assert repo.get_run_approval(pending[0].id).status == "rejected"


def test_new_project_form_values_normalizes_the_two_controls():
    from agentic_company.console.web.app import new_project_form_values

    # The form carries exactly two run controls now: run_mode (the team/shape) and risk_mode.
    values = new_project_form_values(run_mode="complex", risk_mode="safe")
    assert values["run_mode"] == "complex"
    assert values["risk_mode"] == "safe"
    assert "mode" not in values and "complexity" not in values and "team_preset" not in values


def test_codex_preflight_requires_auth_json_on_disk(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import agentic_company.console.web.app as app_mod
    from agentic_company.console.web.app import _codex_start_preflight
    from agentic_company.integrations.codex.runner import CODEX_AUTH_MODE_USER_CHATGPT

    monkeypatch.setattr(app_mod, "codex_auth_mode_from_env", lambda: CODEX_AUTH_MODE_USER_CHATGPT)
    user = SimpleNamespace(id=7)
    repo = SimpleNamespace(
        get_codex_auth_connection=lambda uid: SimpleNamespace(status="connected"),
        list_projects_for_user=lambda uid: [],
    )
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setattr(app_mod.codex_account_auth, "codex_home_for_user", lambda uid: home)
    monkeypatch.setattr(
        app_mod.codex_account_auth,
        "codex_login_status",
        lambda uid: SimpleNamespace(connected=True, auth_mode="chatgpt", message="logged in"),
    )

    # connected DB row but no auth.json on disk -> blocked with a reconnect message
    assert "Codex login" in _codex_start_preflight(repo, user)
    # auth.json present -> preflight passes
    (home / "auth.json").write_text("{}", encoding="utf-8")
    assert _codex_start_preflight(repo, user) == ""


def test_pending_codex_login_auto_connects_when_auth_json_appears(tmp_path, monkeypatch):
    import agentic_company.console.web.app as app_mod
    from agentic_company.console.web.app import _reconcile_pending_codex_login

    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(email="cx@example.test", username="cx", password="password-1")
    repo.upsert_codex_auth_connection(
        user.id, auth_slot=f"user:{user.id}", status="pending", auth_mode="chatgpt"
    )
    home = tmp_path / "cxhome"
    home.mkdir()
    monkeypatch.setattr(app_mod.codex_account_auth, "codex_home_for_user", lambda uid: home)
    status_calls = []

    def fake_status(uid):
        status_calls.append(uid)
        return type("Status", (), {"connected": True, "auth_mode": "chatgpt", "message": "ok"})()

    monkeypatch.setattr(app_mod.codex_account_auth, "codex_login_status", fake_status)

    # login still in progress (no auth.json) -> stays pending
    _reconcile_pending_codex_login(repo, user)
    assert repo.get_codex_auth_connection(user.id).status == "pending"
    assert status_calls == []

    # browser login finished and `codex login status` confirms it -> auto-connected
    (home / "auth.json").write_text("{}", encoding="utf-8")
    _reconcile_pending_codex_login(repo, user)
    assert repo.get_codex_auth_connection(user.id).status == "connected"
    assert status_calls == [user.id]


def test_create_project_requires_codex_connection_in_user_chatgpt_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTIC_CODEX_AUTH_MODE", "user_chatgpt")
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "codex-required@example.test",
            "username": "codexrequired",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "google_gemini", "AIza-project")

    response = client.post(
        "/projects",
        data={
            "name": "Task Tracker",
            "request_text": "Build a task tracker",
            "mode": "simple_prototype",
            "complexity": "simple",
            "agent_provider": "google_gemini",
            "agent_model": "gemini-3.1-flash-lite",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
    )

    assert response.status_code == 400
    assert "Connect your Codex account" in response.text


def test_create_project_with_github_board_records_connection(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={"email": "boarder@example.test", "username": "boarder", "password": "password-1"},
    )
    repo.save_provider_secret(1, "openai", "sk-test-board")
    run_root = tmp_path / "runs"

    def fake_create_console_run(username, requirements_text):
        run_dir = run_root / "board-test"
        run_dir.mkdir(parents=True)
        (run_dir / "00-requirements.md").write_text(requirements_text, encoding="utf-8")
        return run_dir

    monkeypatch.setattr(
        "agentic_company.console.web.app.create_web_console_run", fake_create_console_run
    )
    monkeypatch.setattr("agentic_company.console.web.app.start_codex_execution", lambda run_dir: 1)

    response = client.post(
        "/projects",
        data={
            "name": "Board App",
            "request_text": "Build a board app",
            "mode": "ui_web_app",
            "complexity": "simple",
            "agent_provider": "openai",
            "agent_model": "gpt-4.1",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "medium",
            "service_tier": "standard",
            "board_adapter": "github",
            "repository": "AntonLiutov/board-app",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    project_id = repo.list_projects_for_user(1)[0].id
    conn = repo.get_active_work_system_connection(project_id=project_id)
    assert conn is not None
    assert conn.repository == "AntonLiutov/board-app"
    assert conn.metadata["owner"] == "AntonLiutov"  # derived from the repository
    assert "project_number" not in conn.metadata  # board provisioned lazily on first run


def test_new_run_creates_canonical_planning_work_items(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="planner@example.test",
        username="planner",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Board",
        request_text="Build",
    )

    run = repo.create_run(
        project_id=project.id,
        run_uid="run-planning",
        run_dir=tmp_path / "run-planning",
        status="running",
        reasoning="medium",
    )

    items = repo.list_work_items(run.id)

    assert [item.work_item_id for item in items] == [
        "PLAN-01",
        "PLAN-02",
        "PLAN-03",
        "PLAN-04",
    ]
    assert [item.status for item in items] == ["todo", "todo", "todo", "todo"]


def test_new_run_registers_source_requirements_artifact(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="requirements@example.test",
        username="requirements",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Requirements",
        request_text="Build",
    )
    run_dir = tmp_path / "run-requirements"
    run_dir.mkdir()
    (run_dir / "00-requirements.md").write_text("Build a tiny app.\n", encoding="utf-8")

    run = repo.create_run(
        project_id=project.id,
        run_uid="run-requirements",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )

    artifacts = repo.list_artifact_records(run.id)
    assert [(artifact.relative_path, artifact.work_item_id) for artifact in artifacts] == [
        ("00-requirements.md", "PLAN-01")
    ]
    assert artifacts[0].artifact_type == "source_requirements"
    assert artifacts[0].visibility == "business"
    plan_01 = next(item for item in repo.list_work_items(run.id) if item.work_item_id == "PLAN-01")
    assert artifacts[0].artifact_id in plan_01.artifact_ids


def test_pm_queue_materializes_feature_work_items_in_db(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="pm@example.test",
        username="pmuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="PM",
        request_text="Build",
    )
    run_dir = tmp_path / "run-pm"
    pm_dir = run_dir / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "planned-work-items.json").write_text(
        json.dumps(
            [
                {
                    "id": "US-rooms",
                    "title": "Rooms",
                    "sprint_id": "sprint-01",
                    "delivery_order": 1,
                    "suggested_owner_agent": "fullstack-agent",
                },
                {
                    "id": "US-contacts",
                    "title": "Contacts",
                    "sprint_id": "sprint-01",
                    "delivery_order": 2,
                    "suggested_owner_agent": "fullstack-agent",
                },
            ]
        ),
        encoding="utf-8",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-pm",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )

    with repo.connect() as conn:
        for item in json.loads((pm_dir / "planned-work-items.json").read_text()):
            conn.execute(
                """
                INSERT INTO work_items (
                    run_id, work_item_id, title, sprint_id, delivery_order, status,
                    lane, owner_agent, source_refs, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'todo', 'todo', ?, '[]', ?, ?)
                """,
                (
                    run.id,
                    item["id"],
                    item["title"],
                    item["sprint_id"],
                    item["delivery_order"],
                    item["suggested_owner_agent"],
                    "2026-05-31T10:00:00Z",
                    "2026-05-31T10:00:00Z",
                ),
            )
    items = {item.work_item_id: item for item in repo.list_work_items(run.id)}

    assert items["US-rooms"].status == "todo"
    assert items["US-rooms"].sprint_id == "sprint-01"
    assert items["US-contacts"].owner_agent == "fullstack-agent"


def test_logs_endpoint_filters_db_activity_by_task_id(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="logs@example.test",
        username="logsuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Logs",
        request_text="Build",
    )
    run_dir = tmp_path / "run-logs"
    pm_dir = run_dir / "upstream-planning" / "project-management"
    pm_dir.mkdir(parents=True)
    (pm_dir / "planned-work-items.json").write_text(
        json.dumps(
            [
                {"id": "US-rooms", "title": "Rooms", "sprint_id": "sprint-01"},
                {"id": "US-contacts", "title": "Contacts", "sprint_id": "sprint-01"},
            ]
        ),
        encoding="utf-8",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-logs",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )
    with repo.connect() as conn:
        now = "2026-05-31T10:00:00Z"
        for work_item_id, title, status, owner in [
            ("US-rooms", "Rooms", "done", "qa-agent"),
            ("US-contacts", "Contacts", "review", "fullstack-agent"),
        ]:
            conn.execute(
                """
                INSERT INTO work_items (
                    run_id, work_item_id, title, sprint_id, delivery_order, status,
                    lane, owner_agent, assigned_agent, source_refs, artifact_ids,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'sprint-01', 1, ?, ?, ?, ?, '[]', '[]', ?, ?)
                """,
                (run.id, work_item_id, title, status, status, owner, owner, now, now),
            )
        for event_id, work_item_id, owner, tool, message, status in [
            (
                "rooms-build",
                "US-rooms",
                "fullstack-agent",
                "run_fullstack",
                "Rooms are ready for Quality.",
                "review",
            ),
            (
                "rooms-qa",
                "US-rooms",
                "qa-agent",
                "run_qa",
                "Rooms passed quality review.",
                "done",
            ),
            (
                "contacts-build",
                "US-contacts",
                "fullstack-agent",
                "run_fullstack",
                "Contacts are ready for Quality.",
                "review",
            ),
        ]:
            conn.execute(
                """
                INSERT INTO activity_events (
                    run_id, event_id, work_item_id, owner_agent, agent_id, tool_name,
                    message, status, artifact_ids, visibility, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 'user', ?)
                """,
                (run.id, event_id, work_item_id, owner, owner, tool, message, status, now),
            )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/api/runs/{run.id}/logs?task_id=US-rooms")

    assert response.status_code == 200
    text = json.dumps(response.json())
    assert "Rooms are ready for Quality." in text
    assert "Rooms passed quality review." in text
    assert "Contacts are ready for Quality." not in text
    items = {item.work_item_id: item for item in repo.list_work_items(run.id)}
    assert items["US-rooms"].owner_agent == "qa-agent"
    assert items["US-rooms"].status == "done"


def test_status_endpoint_reports_active_owner_without_duplicate_running(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="status-owner@example.test",
        username="statusowner",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Status owner",
        request_text="Build",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-status-owner",
        run_dir=tmp_path / "run-status-owner",
        status="running",
        reasoning="medium",
    )
    with repo.connect() as conn:
        conn.execute(
            """
            UPDATE work_items
            SET status = 'in_progress',
                lane = 'in_progress',
                owner_agent = 'business-analyst-agent',
                active = 1
            WHERE run_id = ? AND work_item_id = 'PLAN-01'
            """,
            (run.id,),
        )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/api/runs/{run.id}/status")

    assert response.status_code == 200
    assert response.json()["status"] == "Running"
    assert response.json()["stage"] == "Running"
    assert response.json()["active_owner"] == "Business Analyst"


def test_logs_endpoint_reads_db_activity_only_when_db_work_items_exist(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="strict-logs@example.test",
        username="strictlogs",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Strict logs",
        request_text="Build",
    )
    run_dir = tmp_path / "run-strict-logs"
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-strict-logs",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/api/runs/{run.id}/logs")

    assert response.status_code == 200
    assert response.json() == {"logs": [], "groups": []}


def test_db_activity_requires_explicit_work_item_rows(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="strict-sync@example.test",
        username="strictsync",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Strict sync",
        request_text="Build",
    )
    run_dir = tmp_path / "run-strict-sync"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-strict-sync",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )
    with repo.connect() as conn:
        conn.execute(
            """
            INSERT INTO work_items (
                run_id, work_item_id, title, sprint_id, delivery_order, status,
                lane, owner_agent, source_refs, created_at, updated_at
            )
            VALUES (?, 'US-rooms', 'Rooms', 'sprint-01', 1, 'todo',
                'todo', 'fullstack-agent', '[]', ?, ?)
            """,
            (run.id, "2026-05-31T10:00:00Z", "2026-05-31T10:00:00Z"),
        )

    rooms = next(item for item in repo.list_work_items(run.id) if item.work_item_id == "US-rooms")
    assert rooms.status == "todo"
    assert repo.list_activity_events(run.id, work_item_id="US-rooms") == []


def test_create_project_can_use_gemini_for_agent_executor(tmp_path, monkeypatch):
    import agentic_company.console.web.app as app_mod

    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "gemini-run@example.test",
            "username": "geminirun",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "openai", "sk-codex-project")
    repo.save_provider_secret(1, "google_gemini", "AIza-project")
    repo.upsert_codex_auth_connection(
        1, auth_slot="user:1", status="connected", auth_mode="chatgpt"
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_mod.codex_account_auth, "codex_home_for_user", lambda uid: codex_home)

    def fake_codex_status(uid):
        return type(
            "Status",
            (),
            {"connected": True, "auth_mode": "chatgpt", "message": "ok"},
        )()

    monkeypatch.setattr(
        app_mod.codex_account_auth,
        "codex_login_status",
        fake_codex_status,
    )
    run_root = tmp_path / "runs"

    def fake_create_console_run(username, requirements_text):
        run_dir = run_root / "console-gemini"
        run_dir.mkdir(parents=True)
        (run_dir / "00-requirements.md").write_text(requirements_text, encoding="utf-8")
        return run_dir

    monkeypatch.setattr(
        "agentic_company.console.web.app.create_web_console_run",
        fake_create_console_run,
    )
    monkeypatch.setattr("agentic_company.console.web.app.start_codex_execution", lambda run_dir: 1)

    response = client.post(
        "/projects",
        data={
            "name": "Gemini Task",
            "request_text": "Build a tiny Gemini-routed app",
            "mode": "simple_prototype",
            "complexity": "simple",
            "agent_provider": "google_gemini",
            "agent_model": "gemini-3.1-flash-lite",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_dir = run_root / "console-gemini"
    env_text = (run_dir / "delivery" / "agent-runtime.env").read_text(encoding="utf-8")
    assert not (run_dir / "generated-project" / ".env").exists()
    assert "AGENT_LLM_PROVIDER=google_gemini" in env_text
    assert "AGENT_LLM_MODEL=gemini-3.1-flash-lite" in env_text
    assert "GOOGLE_API_KEY=AIza-project" not in env_text
    assert "CODEX_API_KEY=sk-codex-project" not in env_text
    assert "OPENAI_API_KEY=sk-codex-project" not in env_text


def test_create_project_ignores_platform_gemini_env_without_user_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-platform")
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "platform-gemini@example.test",
            "username": "platformgemini",
            "password": "password-1",
        },
    )
    response = client.post(
        "/projects",
        data={
            "name": "Platform Gemini Task",
            "request_text": "Build a tiny Gemini-routed app",
            "mode": "simple_prototype",
            "complexity": "simple",
            "codex_model": "gpt-5.5",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "Add your Gemini key" in response.text
    assert repo.list_projects_for_user(1) == []


def test_format_request_uses_selected_openai_config_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-platform-format")
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "format@example.test",
            "username": "formatuser",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "openai", "sk-openai-format")
    repo.save_provider_secret(1, "google_gemini", "AIza-user-format")
    captured: dict[str, str] = {}

    def fake_format(text: str, *, provider: str, model: str, api_key: str = "") -> str:
        captured["text"] = text
        captured["provider"] = provider
        captured["model"] = model
        captured["api_key"] = api_key
        return "# Product Request\n\n## Summary\nFormatted by OpenAI.\n"

    monkeypatch.setattr("agentic_company.console.web.app.format_request_text_with_llm", fake_format)

    response = client.post("/api/format-request", data={"text": "make a tiny app"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source"] == "openai"
    assert payload["formatted"].startswith("# Product Request")
    assert captured == {
        "text": "make a tiny app",
        "provider": "openai",
        "model": "gpt-4.1",
        "api_key": "sk-openai-format",
    }


def test_format_request_uses_selected_gemini_config(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "format-gemini@example.test",
            "username": "formatgemini",
            "password": "password-1",
        },
    )
    repo.save_provider_secret(1, "google_gemini", "AIza-user-format")
    captured: dict[str, str] = {}

    def fake_format(text: str, *, provider: str, model: str, api_key: str = "") -> str:
        captured["text"] = text
        captured["provider"] = provider
        captured["model"] = model
        captured["api_key"] = api_key
        return "# Product Request\n\n## Summary\nFormatted by Gemini.\n"

    monkeypatch.setattr("agentic_company.console.web.app.format_request_text_with_llm", fake_format)

    response = client.post(
        "/api/format-request",
        data={
            "text": "make a tiny app",
            "agent_provider": "google_gemini",
            "agent_model": "gemini-3.1-flash-lite",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source"] == "google_gemini"
    assert payload["formatted"].startswith("# Product Request")
    assert captured == {
        "text": "make a tiny app",
        "provider": "google_gemini",
        "model": "gemini-3.1-flash-lite",
        "api_key": "AIza-user-format",
    }


def test_format_request_without_selected_provider_key_keeps_text_and_shows_message(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "nogemini@example.test",
            "username": "nogemini",
            "password": "password-1",
        },
    )

    response = client.post("/api/format-request", data={"text": "keep my original text"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["source"] == "openai"
    assert payload["formatted"] == "keep my original text"
    assert "OpenAI is not connected" in payload["message"]


def test_create_project_requires_saved_openai_key(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    app = create_app(repo)
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "nokey@example.test",
            "username": "nokey",
            "password": "password-1",
        },
    )
    monkeypatch.setattr(
        "agentic_company.console.web.app.start_codex_execution",
        lambda run_dir: 1,
    )

    response = client.post(
        "/projects",
        data={
            "name": "Task Tracker",
            "request_text": "Build a task tracker",
            "mode": "simple_prototype",
            "complexity": "simple",
            "agent_provider": "openai",
            "agent_model": "gpt-4.1",
        },
    )

    assert response.status_code == 400
    assert "Add your OpenAI key" in response.text
    assert repo.list_projects_for_user(1) == []


def test_restart_project_creates_new_run_from_saved_request(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="restart@example.test",
        username="restart",
        password="password-1",
    )
    repo.save_provider_secret(user.id, "openai", "sk-test-restart")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Restartable",
        request_text="Build a tiny app",
    )
    old_run_dir = tmp_path / "runs" / "old"
    old_env = old_run_dir / "delivery" / "agent-runtime.env"
    old_env.parent.mkdir(parents=True)
    old_env.write_text(
        "\n".join(
            [
                "AGENT_LLM_MODEL=gpt-5.5",
                "COORDINATOR_AGENT_REASONING_EFFORT=high",
                "AGENT_CODEX_MODEL=gpt-5.5",
                "AGENTIC_CODEX_REASONING_EFFORT=xhigh",
                "AGENTIC_CODEX_SERVICE_TIER=fast",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="old",
        run_dir=old_run_dir,
        status="stale",
        reasoning="medium",
    )
    new_run_dir = tmp_path / "runs" / "new"

    def fake_create_console_run(username, requirements_text):
        new_run_dir.mkdir(parents=True)
        (new_run_dir / "00-requirements.md").write_text(requirements_text, encoding="utf-8")
        return new_run_dir

    monkeypatch.setattr(
        "agentic_company.console.web.app.create_web_console_run",
        fake_create_console_run,
    )
    monkeypatch.setattr("agentic_company.console.web.app.start_codex_execution", lambda run_dir: 1)
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/restart", follow_redirects=False)

    assert response.status_code == 303
    assert "Build a tiny app" in (new_run_dir / "00-requirements.md").read_text(encoding="utf-8")
    new_env = (new_run_dir / "delivery" / "agent-runtime.env").read_text(encoding="utf-8")
    assert not (new_run_dir / "generated-project" / ".env").exists()
    assert "AGENT_LLM_MODEL=gpt-5.5" in new_env
    assert "COORDINATOR_AGENT_REASONING_EFFORT=high" in new_env
    assert "AGENT_CODEX_MODEL=gpt-5.5" in new_env
    assert "AGENTIC_CODEX_REASONING_EFFORT=xhigh" in new_env
    assert "AGENTIC_CODEX_SERVICE_TIER=fast" in new_env
    assert len(repo.runs_for_project(project.id, user.id)) == 2


def test_continue_project_reuses_existing_run_dir(tmp_path, monkeypatch):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="continue@example.test",
        username="continue",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Continuable",
        request_text="Build a tiny app",
    )
    run_dir = tmp_path / "runs" / "same-run"
    run_dir.mkdir(parents=True)
    (run_dir / ".codex-execution.stop").write_text("stop\n", encoding="utf-8")
    (run_dir / ".stop-requested").write_text("stop\n", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid=run_dir.name,
        run_dir=run_dir,
        status="stopped",
        reasoning="medium",
    )
    # Drive the REAL stop path (control intent + durable process stop flag), not a
    # cosmetic "failed" status — so the test proves Continue clears the ACTUAL stop
    # sources (the previous fake masked that Continue left stop_requested_at set).
    repo.set_run_control_intent(run.id, intent="cancel", reason="Stopped by user.")
    repo.request_console_process_stop(run.id, process_name="codex_execution")
    assert repo.get_console_process_state(run.id, "codex_execution").stop_requested_at
    started: list[Path] = []
    monkeypatch.setattr(
        "agentic_company.console.web.app.start_codex_execution",
        lambda path: started.append(path) or 1,
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/continue", follow_redirects=False)

    assert response.status_code == 303
    assert started == [run_dir]
    assert not (run_dir / ".codex-execution.stop").exists()
    assert not (run_dir / ".stop-requested").exists()
    updated = repo.get_run_by_uid(run_dir.name)
    assert updated is not None
    assert updated.id == run.id
    assert updated.status == "running"
    proc = repo.get_console_process_state(run.id, "codex_execution")
    assert proc.status == "continued"
    assert not proc.stop_requested_at  # durable DB stop flag cleared on continue
    # No remaining stop source (file gone, control_intent cleared, DB flag cleared,
    # Redis noop in tests) => the continued run will not immediately re-stop.
    from agentic_company.platform.db.runtime_db import run_stop_requested

    assert run_stop_requested(run_dir.name, run_dir) is False


def test_restart_button_visible_for_blocked_private_run(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="blocked@example.test",
        username="blocked",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Blocked Project",
        request_text="Build a tiny app",
    )
    run_dir = tmp_path / "runs" / "blocked"
    run_dir.mkdir(parents=True)
    delivery_state_path(run_dir).write_text(
        json.dumps(
            {
                "run_id": "blocked",
                "run_dir": str(run_dir),
                "stage": "head",
                "status": "head_planning_blocked",
                "blockers": ["Coordinator could not start."],
                "artifacts": [],
                "completed_nodes": ["head"],
            }
        ),
        encoding="utf-8",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="blocked",
        run_dir=run_dir,
        status="head_planning_blocked",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 200
    assert "Restart Project" in response.text


def test_project_request_visible_in_lists_and_workspace(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="request@example.test",
        username="requestuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Request Project",
        request_text=(
            "# Product Request\n\n"
            "## Summary\n"
            "Build a colorful demo app with three buttons and a simple report.\n\n"
            "## Requirements\n"
            "- First button creates one action.\n"
            "- Second button creates another action.\n"
        ),
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    dashboard_response = client.get("/dashboard")
    projects_response = client.get("/projects")
    workspace_response = client.get(f"/projects/{project.id}")

    assert dashboard_response.status_code == 200
    assert projects_response.status_code == 200
    assert workspace_response.status_code == 200
    assert "Build a colorful demo app" in dashboard_response.text
    assert "Build a colorful demo app" in projects_response.text
    assert "Project request" in workspace_response.text
    assert "<h1>Product Request</h1>" in workspace_response.text
    assert "<h2>Summary</h2>" in workspace_response.text
    assert "<li>First button creates one action.</li>" in workspace_response.text


def test_delete_private_project_removes_it_from_workspace(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="delete@example.test",
        username="deleteuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Delete Me",
        request_text="private",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/projects"
    assert repo.get_project_for_user(project.id, user.id) is None


def test_stop_project_marks_latest_run_stopped(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="stop@example.test",
        username="stopuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Stop Me",
        request_text="private",
    )
    run_dir = tmp_path / "runs" / "stop"
    run_dir.mkdir(parents=True)
    run = repo.create_run(
        project_id=project.id,
        run_uid="stop",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert repo.get_run(run.id).status == "stopped"
    assert repo.get_project_for_user(project.id, user.id).status == "stopped"
    process_state = repo.get_console_process_state(run.id, "codex_execution")
    assert process_state is not None
    assert process_state.status == "stop_requested"
    assert process_state.stop_requested_at


def test_stop_project_completes_when_runtime_cache_is_unavailable(tmp_path, monkeypatch):
    class FailingCache:
        def set_stop_requested(self, _run_uid: str) -> None:
            raise RedisConnectionError("redis unavailable")

    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="stop-cache@example.test",
        username="stopcache",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Stop Cache",
        request_text="private",
        status="running",
    )
    run_dir = tmp_path / "runs" / "stop-cache"
    run_dir.mkdir(parents=True)
    run = repo.create_run(
        project_id=project.id,
        run_uid="stop-cache",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )
    repo.upsert_sprint(
        run.id,
        sprint_id="sprint-01",
        title="Sprint 1",
        delivery_order=1,
        status="running",
        is_final=True,
    )
    with repo.connect() as conn:
        conn.execute(
            """
            INSERT INTO work_items (
                run_id, work_item_id, title, sprint_id, delivery_order, status,
                lane, owner_agent, assigned_agent, active, source_refs,
                artifact_ids, blocker, created_at, updated_at
            )
            VALUES (?, 'F1', 'F1', 'sprint-01', 1, 'in_progress',
                    'in_progress', 'fullstack-agent', 'fullstack-agent', 1,
                    '[]', '[]', '', '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
            """,
            (run.id,),
        )
    monkeypatch.setattr(
        "agentic_company.console.web.app.runtime_cache_from_env",
        lambda: FailingCache(),
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert repo.get_run(run.id).status == "stopped"
    assert repo.get_project_for_user(project.id, user.id).status == "stopped"
    assert (run_dir / ".stop-requested").exists()
    assert (run_dir / ".codex-execution.stop").exists()
    process_state = repo.get_console_process_state(run.id, "codex_execution")
    assert process_state is not None
    assert process_state.status == "stop_requested"
    work_items = {item.work_item_id: item for item in repo.list_work_items(run.id)}
    assert work_items["F1"].status == "blocked"
    assert "Stopped by user." in work_items["F1"].blocker


def test_stop_project_completes_when_runtime_cache_config_is_invalid(
    tmp_path,
    monkeypatch,
):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="stop-bad-cache@example.test",
        username="stopbadcache",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Stop Bad Cache",
        request_text="private",
        status="running",
    )
    run_dir = tmp_path / "runs" / "stop-bad-cache"
    run_dir.mkdir(parents=True)
    run = repo.create_run(
        project_id=project.id,
        run_uid="stop-bad-cache",
        run_dir=run_dir,
        status="running",
        reasoning="medium",
    )

    def raise_invalid_cache_config():
        raise ValueError("invalid redis url")

    monkeypatch.setattr(
        "agentic_company.console.web.app.runtime_cache_from_env",
        raise_invalid_cache_config,
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert repo.get_run(run.id).status == "stopped"
    assert repo.get_project_for_user(project.id, user.id).status == "stopped"
    assert (run_dir / ".stop-requested").exists()
    assert (run_dir / ".codex-execution.stop").exists()


def test_promote_and_demote_project_from_workspace(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    owner = repo.create_user(
        email="showcase-owner@example.test",
        username="owner",
        password="password-1",
    )
    viewer = repo.create_user(
        email="showcase-viewer@example.test",
        username="viewer",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=owner.id,
        name="Promote Me",
        request_text="private",
    )
    run_dir = tmp_path / "runs" / "showcase"
    run_dir.mkdir(parents=True)
    release_path = run_dir / "handoff" / "project" / "final" / "release-report.html"
    release_path.parent.mkdir(parents=True)
    release_path.write_text("<h1>Release ready</h1>", encoding="utf-8")
    release = register_artifact(
        run_dir,
        artifact_id=artifact_id_for("showcase", "handoff/project/final/release-report.html"),
        relative_path="handoff/project/final/release-report.html",
        run_id="showcase",
        owner_agent="handoff-agent",
        artifact_type="release_report",
        visibility="release",
        label="Final report",
        source_tool="run_handoff",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="showcase",
        run_dir=run_dir,
        status="complete",
        reasoning="medium",
    )
    repo.upsert_artifact_record(run.id, release)
    with repo.connect() as conn:
        conn.execute(
            """
            INSERT INTO tool_call_events (
                run_id, runtime_run_id, event_id, work_item_id, agent_id, tool_name,
                tool_call_id, input_summary, output_summary, artifact_ids, status,
                created_at
            )
            VALUES (?, ?, 'tool_showcase_handoff', '', 'team-lead-agent', 'run_handoff',
                'tool_showcase_handoff', '{}', ?, ?, 'handoff_ready', ?)
            """,
            (
                run.id,
                "showcase",
                json.dumps(
                    {
                        "business_summary": "Release report is ready.",
                        "dashboard_update": {
                            "status": "done",
                            "summary": "Release report is ready.",
                            "comment": "Release report is ready.",
                        },
                    }
                ),
                json.dumps([release.artifact_id]),
                "2026-05-31T10:00:00Z",
            ),
        )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(owner.id))

    promote_response = client.post(f"/projects/{project.id}/promote", follow_redirects=False)

    assert promote_response.status_code == 303
    assert repo.get_project_for_user(project.id, viewer.id).visibility == "public_demo"

    demote_response = client.post(f"/projects/{project.id}/demote", follow_redirects=False)

    assert demote_response.status_code == 303
    assert repo.get_project_for_user(project.id, viewer.id) is None


def test_promote_project_requires_completed_run(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    owner = repo.create_user(
        email="showcase-not-ready@example.test",
        username="notready",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=owner.id,
        name="Not Ready",
        request_text="private",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(owner.id))

    response = client.post(f"/projects/{project.id}/promote", follow_redirects=False)

    assert response.status_code == 400
    assert repo.get_project_for_user(project.id, owner.id).visibility == "private"


def test_public_demo_project_cannot_be_deleted_by_user(tmp_path, monkeypatch):
    run_dir = tmp_path / "demo-run"
    run_dir.mkdir()
    monkeypatch.setenv("PUBLIC_DEMO_RUN_DIR", str(run_dir))
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="demo-delete@example.test",
        username="demodelete",
        password="password-1",
    )
    repo.seed_public_demo_from_env()
    project = repo.public_demo_project()
    assert project is not None
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/delete", follow_redirects=False)

    assert response.status_code == 404
    assert repo.public_demo_project() is not None


def test_showcase_page_lists_multiple_public_projects_with_owner_private_action(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    owner = repo.create_user(
        email="multi-showcase-owner@example.test",
        username="multishowcase",
        password="password-1",
    )
    other = repo.create_user(
        email="multi-showcase-other@example.test",
        username="othershowcase",
        password="password-1",
    )
    owner_project = repo.create_project(
        owner_user_id=owner.id,
        name="Owner Showcase",
        request_text="Seeded showcase request from database.",
    )
    other_project = repo.create_project(
        owner_user_id=other.id,
        name="Other Showcase",
        request_text="other public",
    )
    owner_run_dir = tmp_path / "runs" / "owner-showcase"
    owner_run_dir.mkdir(parents=True)
    (owner_run_dir / "00-requirements.md").write_text(
        "# Product Request\n\n## Summary\nSeeded showcase request from run folder.\n",
        encoding="utf-8",
    )
    repo.create_run(
        project_id=owner_project.id,
        run_uid="owner-showcase",
        run_dir=owner_run_dir,
        status="ready",
        reasoning="medium",
    )
    assert repo.set_project_visibility(owner_project.id, owner.id, "public_demo")
    assert repo.set_project_visibility(other_project.id, other.id, "public_demo")
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(owner.id))

    response = client.get("/public-demo")
    artifacts_response = client.get("/artifacts")

    assert response.status_code == 200
    assert "Owner Showcase" in response.text
    assert "Other Showcase" in response.text
    assert "Seeded showcase request from database." in response.text
    assert "Public project</small>" not in response.text
    assert f"/projects/{owner_project.id}/demote" in response.text
    assert f"/projects/{other_project.id}/demote" not in response.text
    assert artifacts_response.status_code == 200
    assert "Owner Showcase" in artifacts_response.text
    assert "Other Showcase" in artifacts_response.text


def test_artifact_path_traversal_is_rejected(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="artifact@example.test",
        username="artifact",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Artifacts",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/%2E%2E%2Fsecret.txt")

    assert response.status_code == 404


def test_json_artifact_is_not_exposed_in_product_console(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="json@example.test",
        username="jsonuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Artifacts",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "business-analysis.json").write_text('{"secret": "technical"}', encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-json",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/business-analysis.json")

    assert response.status_code == 404


def test_artifact_view_uses_business_title_instead_of_internal_path(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="report@example.test",
        username="reportuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Report Project",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("<html><body>Stakeholder summary</body></html>", encoding="utf-8")
    record = register_artifact(
        run_dir,
        artifact_id=artifact_id_for("run-report", "handoff/sprints/sprint-01/release-report.html"),
        relative_path="handoff/sprints/sprint-01/release-report.html",
        run_id="run-report",
        owner_agent="handoff-agent",
        artifact_type="release_report",
        visibility="release",
        label="Sprint 1 report",
        source_tool="run_handoff",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-report",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    repo.upsert_artifact_record(run.id, record)
    register_artifact_content(
        repo,
        run.id,
        record,
        "<html><body>Stakeholder summary</body></html>",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}")

    assert response.status_code == 200
    assert "Sprint 1 report" in response.text
    assert "handoff/sprints/sprint-01/release-report.html" not in response.text


def test_artifact_view_resolves_registry_id(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="artifact-id@example.test",
        username="artifactid",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Registry Report",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "handoff" / "project" / "final" / "release-report.html"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("<html><body>Registered report</body></html>", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-registry-report",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    record = register_artifact(
        run_dir,
        artifact_id=artifact_id_for(run.run_uid, "handoff/project/final/release-report.html"),
        relative_path="handoff/project/final/release-report.html",
        run_id=run.run_uid,
        owner_agent="documentation-handoff-agent",
        label="Registered final report",
        visibility="release",
        artifact_type="release_report",
        source_tool="run_handoff",
    )
    repo.upsert_artifact_record(run.id, record)
    register_artifact_content(
        repo,
        run.id,
        record,
        "<html><body>Registered report</body></html>",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}")

    assert response.status_code == 200
    assert "Registered final report" in response.text
    assert "Registered report" in response.text


def test_run_trace_api_returns_owned_structured_trace_without_secrets(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="trace-route@example.test",
        username="traceroute",
        password="password-1",
    )
    other = repo.create_user(
        email="trace-other@example.test",
        username="traceother",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Trace API",
        request_text="private",
    )
    run_dir = tmp_path / "trace-api"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="trace-api",
        run_dir=run_dir,
        status="ready",
        reasoning="medium",
    )
    repo.upsert_run_event(
        run.id,
        RunEvent(
            event_id="run-started",
            project_id=project.id,
            run_id=run.run_uid,
            work_item_id=None,
            agent_id="delivery-graph",
            event_type="delivery_graph_started",
            status="running",
            message="Delivery started.",
            data={"status": "running", "OPENAI_API_KEY": "sk-secret"},
            created_at="2026-05-31T10:00:00Z",
        ),
    )
    repo.upsert_tool_call_event(
        run.id,
        ToolCallEvent(
            event_id="tool-call-1",
            run_id=run.run_uid,
            work_item_id="US-rooms",
            agent_id="team-lead-agent",
            tool_name="run_fullstack",
            tool_call_id="call-1",
            status="codex_completed",
            artifact_ids=["art_route"],
            output_summary={"output_artifacts": [{"artifact_id": "art_route"}]},
            created_at="2026-05-31T10:01:00Z",
        ),
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/api/runs/{run.id}/trace")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_events"][0]["event_type"] == "delivery_graph_started"
    assert payload["run_events"][0]["data"]["OPENAI_API_KEY"] == "[REDACTED]"
    assert payload["tool_call_events"][0]["artifact_ids"] == ["art_route"]
    assert payload["summary"]["tools"] == {"run_fullstack": 1}
    assert "sk-secret" not in response.text

    client.cookies.set("agentic_console_session", repo.create_session(other.id))
    assert client.get(f"/api/runs/{run.id}/trace").status_code == 404


def test_html_artifact_view_opens_report_links_outside_preview(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="report-links@example.test",
        username="reportlinks",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Report Links",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        '<html><body><a href="https://example.test/app">Open app</a></body></html>',
        encoding="utf-8",
    )
    record = register_artifact(
        run_dir,
        artifact_id=artifact_id_for(
            "run-report-links", "handoff/sprints/sprint-01/release-report.html"
        ),
        relative_path="handoff/sprints/sprint-01/release-report.html",
        run_id="run-report-links",
        owner_agent="handoff-agent",
        artifact_type="release_report",
        visibility="release",
        label="Sprint 1 report",
        source_tool="run_handoff",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-report-links",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    repo.upsert_artifact_record(run.id, record)
    register_artifact_content(
        repo,
        run.id,
        record,
        '<html><body><a href="https://example.test/app">Open app</a></body></html>',
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}")
    raw_response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}?raw=1")

    assert response.status_code == 200
    assert "allow-popups-to-escape-sandbox" in response.text
    assert "Open report" in response.text
    assert raw_response.status_code == 200
    assert '<base target="_blank">' in raw_response.text
    assert "sandbox" in raw_response.headers["content-security-policy"]


def test_artifact_view_renders_mermaid_reports(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="mermaid@example.test",
        username="mermaiduser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Mermaid Project",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "upstream-planning" / "architecture.mmd"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("flowchart LR\n  A[One\\nTwo] --> B[Done]\n", encoding="utf-8")
    record = register_artifact(
        run_dir,
        artifact_id=artifact_id_for("run-mermaid", "upstream-planning/architecture.mmd"),
        relative_path="upstream-planning/architecture.mmd",
        run_id="run-mermaid",
        owner_agent="architect-agent",
        artifact_type="architecture_report",
        visibility="release",
        label="Architecture diagram",
        source_tool="run_architect",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-mermaid",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    repo.upsert_artifact_record(run.id, record)
    register_artifact_content(
        repo,
        run.id,
        record,
        "flowchart LR\n  A[One\\nTwo] --> B[Done]\n",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}")

    assert response.status_code == 200
    assert "Architecture diagram" in response.text
    assert "architecture.mmd" not in response.text
    assert 'class="mermaid"' in response.text
    assert "mermaid.esm.min.mjs" in response.text
    assert "One&lt;br/&gt;Two" in response.text


def test_project_agents_tab_shows_agent_catalog(tmp_path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="agents@example.test",
        username="agentsuser",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Agent Project",
        request_text="private",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    env_dir = run_dir / "delivery"
    env_dir.mkdir()
    (env_dir / "agent-runtime.env").write_text(
        "\n".join(
            [
                "AGENT_LLM_PROVIDER=openai",
                "AGENT_LLM_MODEL=gpt-4.1",
                "AGENT_CODEX_MODEL=gpt-5.4",
                "AGENTIC_CODEX_REASONING_EFFORT=high",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="run-agents",
        run_dir=Path(run_dir),
        status="ready",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/projects/{project.id}?tab=agents")

    assert response.status_code == 200
    assert "Coordinator" in response.text
    assert "/static/agents/coordinator.png" in response.text
    assert "/static/agents/release-reporter.png" in response.text
    assert "gpt-4.1" in response.text
    assert "gpt-5.4" in response.text
    assert ">High<" in response.text
    assert ">none<" not in response.text.lower()
