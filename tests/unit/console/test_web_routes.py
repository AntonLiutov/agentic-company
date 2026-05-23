import json
from pathlib import Path

from fastapi.testclient import TestClient

from agentic_company.console.web.app import create_app
from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifact_registry import register_artifact
from agentic_company.platform.events import write_event
from agentic_company.platform.run_trace import record_tool_call_event


def test_landing_page_renders_public_story(tmp_path):
    app = create_app(ConsoleRepository(tmp_path / "console.db"))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Agentic Delivery Lab" in response.text
    assert "Turn product ideas into shipped demos." in response.text
    assert "Gemini API" in response.text
    assert "Speechmatics" in response.text
    assert "brand/agentic-delivery-lab-cover.png" in response.text


def test_register_dashboard_and_logout(tmp_path):
    app = create_app(ConsoleRepository(tmp_path / "console.db"))
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


def test_new_project_page_renders_dictation_language_picker(tmp_path):
    app = create_app(ConsoleRepository(tmp_path / "console.db"))
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "voice@example.test",
            "username": "voiceuser",
            "password": "password-1",
        },
    )

    response = client.get("/projects/new")

    assert response.status_code == 200
    assert "Dictation language" in response.text
    assert "Planning" in response.text
    assert "Google Gemini" in response.text
    assert "Google Gemini" in response.text
    assert "gemini-3.1-flash-lite" in response.text
    assert "Powered by Speechmatics" in response.text
    assert "English (en)" in response.text
    assert "Italian (it)" in response.text
    assert "data-dictation-language" in response.text


def test_speechmatics_token_endpoint_disabled_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)
    app = create_app(ConsoleRepository(tmp_path / "console.db"))
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "no-voice@example.test",
            "username": "novoice",
            "password": "password-1",
        },
    )

    response = client.post("/api/voice/speechmatics-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["fallback"] == "browser"
    assert "token" not in payload
    italian = next(language for language in payload["languages"] if language["code"] == "it")
    assert italian["name"] == "Italian"
    assert italian["recommended"] is True


def test_speechmatics_token_endpoint_returns_short_lived_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "rc-long-lived-secret")
    monkeypatch.setenv("SPEECHMATICS_RT_URL", "wss://speech.example.test/v2")
    monkeypatch.setattr(
        "agentic_company.console.web.app.create_speechmatics_realtime_token",
        lambda: "short-lived-token",
    )
    app = create_app(ConsoleRepository(tmp_path / "console.db"))
    client = TestClient(app)
    client.post(
        "/register",
        data={
            "email": "voice-token@example.test",
            "username": "voicetoken",
            "password": "password-1",
        },
    )

    response = client.post("/api/voice/speechmatics-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["token"] == "short-lived-token"
    assert payload["rt_url"] == "wss://speech.example.test/v2"
    assert "rc-long-lived-secret" not in response.text


def test_settings_can_save_and_delete_gemini_key(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
    assert "Google Gemini" in settings_response.text
    assert "Built with Gemini API" in settings_response.text
    assert "AIza-demo-secret" not in settings_response.text

    delete_response = client.post("/settings/gemini/delete", follow_redirects=False)

    assert delete_response.status_code == 303
    assert repo.get_provider_secret(1, "google_gemini") is None


def test_private_project_not_visible_to_another_user(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
    repo.init_schema()
    user_a = repo.create_user(email="a@example.test", username="auser", password="password-1")
    user_b = repo.create_user(email="b@example.test", username="buser", password="password-1")
    project = repo.create_project(
        owner_user_id=user_a.id,
        name="Secret",
        request_text="private",
        mode="simple_prototype",
        complexity="simple",
    )
    app = create_app(repo)
    client = TestClient(app)
    token_b = repo.create_session(user_b.id)
    client.cookies.set("agentic_console_session", token_b)

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 404


def test_create_project_starts_run_with_monkeypatched_runtime(tmp_path, monkeypatch):
    repo = ConsoleRepository(tmp_path / "console.db")
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
            "codex_model": "gpt-5.3-codex",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (run_root / "console-test" / "00-requirements.md").exists()
    env_text = (run_root / "console-test" / "generated-project" / ".env").read_text(
        encoding="utf-8"
    )
    assert "OPENAI_API_KEY=sk-test-project" in env_text
    assert "CODEX_API_KEY=sk-test-project" in env_text
    assert "AGENT_LLM_PROVIDER=openai" in env_text
    assert "AGENT_CODEX_MODEL=gpt-5.3-codex" in env_text
    assert "AGENTIC_CODEX_SERVICE_TIER=standard" in env_text
    assert repo.list_projects_for_user(1)[0].name == "Task Tracker"


def test_create_project_can_use_gemini_for_agent_executor(tmp_path, monkeypatch):
    repo = ConsoleRepository(tmp_path / "console.db")
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
            "codex_model": "gpt-5.3-codex",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    env_text = (run_root / "console-gemini" / "generated-project" / ".env").read_text(
        encoding="utf-8"
    )
    assert "AGENT_LLM_PROVIDER=google_gemini" in env_text
    assert "AGENT_LLM_MODEL=gemini-3.1-flash-lite" in env_text
    assert "GOOGLE_API_KEY=AIza-project" in env_text
    assert "CODEX_API_KEY=sk-codex-project" in env_text
    assert "OPENAI_API_KEY=sk-codex-project" in env_text


def test_create_project_can_use_platform_gemini_key(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-platform")
    repo = ConsoleRepository(tmp_path / "console.db")
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
    repo.save_provider_secret(1, "openai", "sk-codex-project")
    run_root = tmp_path / "runs"

    def fake_create_console_run(username, requirements_text):
        run_dir = run_root / "console-platform-gemini"
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
            "name": "Platform Gemini Task",
            "request_text": "Build a tiny Gemini-routed app",
            "mode": "simple_prototype",
            "complexity": "simple",
            "codex_model": "gpt-5.3-codex",
            "codex_reasoning": "medium",
            "service_tier": "standard",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    env_text = (run_root / "console-platform-gemini" / "generated-project" / ".env").read_text(
        encoding="utf-8"
    )
    assert "AGENT_LLM_PROVIDER=google_gemini" in env_text
    assert "GOOGLE_API_KEY=AIza-platform" in env_text


def test_format_request_uses_gemini_key_not_openai_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_FORMATTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-platform-format")
    repo = ConsoleRepository(tmp_path / "console.db")
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
    captured: dict[str, str] = {}

    def fake_format(text: str, *, api_key: str = "") -> str:
        captured["text"] = text
        captured["api_key"] = api_key
        return "# Product Request\n\n## Summary\nFormatted by Gemini.\n"

    monkeypatch.setattr("agentic_company.console.web.app.format_request_text_with_llm", fake_format)

    response = client.post("/api/format-request", data={"text": "make a tiny app"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source"] == "gemini"
    assert payload["formatted"].startswith("# Product Request")
    assert captured == {"text": "make a tiny app", "api_key": "AIza-platform-format"}


def test_format_request_without_gemini_keeps_text_and_shows_message(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_FORMATTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    repo = ConsoleRepository(tmp_path / "console.db")
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
    assert payload["source"] == "gemini"
    assert payload["formatted"] == "keep my original text"
    assert "not configured" in payload["message"]


def test_create_project_requires_saved_openai_key(tmp_path, monkeypatch):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        },
    )

    assert response.status_code == 400
    assert "Add your OpenAI key" in response.text
    assert repo.list_projects_for_user(1) == []


def test_restart_project_creates_new_run_from_saved_request(tmp_path, monkeypatch):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    old_run_dir = tmp_path / "runs" / "old"
    old_env = old_run_dir / "generated-project" / ".env"
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
        mode="simple_prototype",
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
    new_env = (new_run_dir / "generated-project" / ".env").read_text(encoding="utf-8")
    assert "AGENT_LLM_MODEL=gpt-5.5" in new_env
    assert "COORDINATOR_AGENT_REASONING_EFFORT=high" in new_env
    assert "AGENT_CODEX_MODEL=gpt-5.5" in new_env
    assert "AGENTIC_CODEX_REASONING_EFFORT=xhigh" in new_env
    assert "AGENTIC_CODEX_SERVICE_TIER=fast" in new_env
    assert len(repo.runs_for_project(project.id, user.id)) == 2


def test_restart_button_visible_for_blocked_private_run(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "runs" / "blocked"
    run_dir.mkdir(parents=True)
    (run_dir / ".delivery-state.json").write_text(
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
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/projects/{project.id}")

    assert response.status_code == 200
    assert "Restart Project" in response.text


def test_project_request_visible_in_lists_and_workspace(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
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
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/projects"
    assert repo.get_project_for_user(project.id, user.id) is None


def test_stop_project_marks_latest_run_stopped(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "runs" / "stop"
    run_dir.mkdir(parents=True)
    run = repo.create_run(
        project_id=project.id,
        run_uid="stop",
        run_dir=run_dir,
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.post(f"/projects/{project.id}/stop", follow_redirects=False)

    assert response.status_code == 303
    assert repo.get_run(run.id).status == "stopped"
    assert repo.get_project_for_user(project.id, user.id).status == "stopped"
    assert (run_dir / ".stop-requested").exists()


def test_promote_and_demote_project_from_workspace(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "runs" / "showcase"
    run_dir.mkdir(parents=True)
    (run_dir / ".delivery-state.json").write_text(
        json.dumps(
            {
                "run_id": "showcase",
                "run_dir": str(run_dir),
                "stage": "head",
                "status": "head_delivery_completed",
                "repair_attempts": 0,
                "max_repair_attempts": 5,
                "artifacts": [],
                "blockers": [],
                "auto_confirmations": [],
                "completed_nodes": [],
            }
        ),
        encoding="utf-8",
    )
    repo.create_run(
        project_id=project.id,
        run_uid="showcase",
        run_dir=run_dir,
        status="head_delivery_completed",
        mode="simple_prototype",
        reasoning="medium",
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
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
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
    repo = ConsoleRepository(tmp_path / "console.db")
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
    repo = ConsoleRepository(tmp_path / "console.db")
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
        request_text="",
        mode="simple_prototype",
        complexity="simple",
    )
    other_project = repo.create_project(
        owner_user_id=other.id,
        name="Other Showcase",
        request_text="other public",
        mode="simple_prototype",
        complexity="simple",
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
        mode="simple_prototype",
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
    assert "Seeded showcase request from run folder" in response.text
    assert "Public project</small>" not in response.text
    assert f"/projects/{owner_project.id}/demote" in response.text
    assert f"/projects/{other_project.id}/demote" not in response.text
    assert artifacts_response.status_code == 200
    assert "Owner Showcase" in artifacts_response.text
    assert "Other Showcase" in artifacts_response.text


def test_artifact_path_traversal_is_rejected(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/%2E%2E%2Fsecret.txt")

    assert response.status_code == 404


def test_json_artifact_is_not_exposed_in_product_console(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "business-analysis.json").write_text('{"secret": "technical"}', encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-json",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/business-analysis.json")

    assert response.status_code == 404


def test_artifact_view_uses_business_title_instead_of_internal_path(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("<html><body>Stakeholder summary</body></html>", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-report",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/handoff/sprints/sprint-01/release-report.html")

    assert response.status_code == 200
    assert "Sprint 1 report" in response.text
    assert "handoff/sprints/sprint-01/release-report.html" not in response.text


def test_artifact_view_resolves_registry_id(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
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
        mode="simple_prototype",
        reasoning="medium",
    )
    record = register_artifact(
        run_dir,
        relative_path="handoff/project/final/release-report.html",
        run_id=run.run_uid,
        owner_agent="documentation-handoff-agent",
        label="Registered final report",
        visibility="release",
        artifact_type="release_report",
    )
    repo.upsert_artifact_record(run.id, record)
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/by-id/{record.artifact_id}")

    assert response.status_code == 200
    assert "Registered final report" in response.text
    assert "Registered report" in response.text


def test_run_trace_api_returns_owned_structured_trace_without_secrets(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "trace-api"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="trace-api",
        run_dir=run_dir,
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    write_event(
        run_dir / "events.jsonl",
        run.run_uid,
        "delivery-graph",
        "delivery_graph_started",
        {"status": "running", "OPENAI_API_KEY": "sk-secret"},
    )
    record_tool_call_event(
        run_dir,
        run_id=run.run_uid,
        agent_id="team-lead-agent",
        tool_name="run_fullstack",
        tool_call_id="call-1",
        status="codex_completed",
        output_summary={"output_artifacts": [{"artifact_id": "art_route"}]},
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
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "handoff" / "sprints" / "sprint-01" / "release-report.html"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        '<html><body><a href="https://example.test/app">Open app</a></body></html>',
        encoding="utf-8",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-report-links",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/handoff/sprints/sprint-01/release-report.html")
    raw_response = client.get(
        f"/artifacts/{run.id}/handoff/sprints/sprint-01/release-report.html?raw=1"
    )

    assert response.status_code == 200
    assert "allow-popups-to-escape-sandbox" in response.text
    assert "Open report" in response.text
    assert raw_response.status_code == 200
    assert '<base target="_blank">' in raw_response.text


def test_artifact_view_renders_mermaid_reports(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    report_path = run_dir / "upstream-planning" / "architecture.mmd"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("flowchart LR\n  A[One\\nTwo] --> B[Done]\n", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-mermaid",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
        reasoning="medium",
    )
    app = create_app(repo)
    client = TestClient(app)
    client.cookies.set("agentic_console_session", repo.create_session(user.id))

    response = client.get(f"/artifacts/{run.id}/upstream-planning/architecture.mmd")

    assert response.status_code == 200
    assert "Architecture diagram" in response.text
    assert "architecture.mmd" not in response.text
    assert 'class="mermaid"' in response.text
    assert "mermaid.esm.min.mjs" in response.text
    assert "One&lt;br/&gt;Two" in response.text


def test_project_agents_tab_shows_agent_catalog(tmp_path):
    repo = ConsoleRepository(tmp_path / "console.db")
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
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repo.create_run(
        project_id=project.id,
        run_uid="run-agents",
        run_dir=Path(run_dir),
        status="ready",
        mode="simple_prototype",
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
    assert "OpenAI or Gemini" in response.text
    assert ">none<" not in response.text.lower()
