from agentic_company.console.web.db import ConsoleRepository


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
    assert repo.get_project_for_user(project.id, user.id) is not None


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
    assert project.id in {item.id for item in repo.list_projects_for_user(owner.id)}

    assert repo.set_project_visibility(project.id, owner.id, "private")
    assert repo.get_project_for_user(project.id, viewer.id) is None
