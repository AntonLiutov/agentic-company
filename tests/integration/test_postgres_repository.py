import os
from pathlib import Path
from uuid import uuid4

import pytest

from agentic_company.console.web.db import ConsoleRepository


@pytest.mark.integration
def test_console_repository_smoke_against_postgres_url(tmp_path, monkeypatch):
    database_url = os.getenv("AGENTIC_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("Set AGENTIC_TEST_DATABASE_URL to run the Postgres repository smoke test.")

    monkeypatch.setenv("AGENTIC_DATABASE_URL", database_url)
    repo = ConsoleRepository(database_url=database_url)
    repo.init_schema()
    suffix = uuid4().hex[:8]

    user = repo.create_user(
        email=f"pg-{suffix}@example.test",
        username=f"pg_{suffix}",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Postgres Smoke",
        request_text="Smoke",
        mode="ui_web_app",
        complexity="small",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid=f"pg-run-{suffix}",
        run_dir=Path(tmp_path) / f"pg-run-{suffix}",
        status="running",
        mode="ui_web_app",
        reasoning="medium",
    )
    repo.append_raw_log_event(
        run.id,
        work_item_id="PLAN-01",
        sprint_id="planning",
        agent_id="business-analyst-agent",
        tool_name="codex_exec",
        tool_call_id="pg-call-1",
        seq=1,
        message="postgres smoke log",
    )

    assert repo.get_run(run.id).run_uid == run.run_uid
    assert [item.work_item_id for item in repo.list_work_items(run.id)][:4] == [
        "PLAN-01",
        "PLAN-02",
        "PLAN-03",
        "PLAN-04",
    ]
    assert repo.list_raw_log_events(run.id, work_item_id="PLAN-01")[0].message == (
        "postgres smoke log"
    )


@pytest.mark.integration
def test_get_active_work_system_connection_prefers_run_scope(tmp_path, monkeypatch):
    database_url = os.getenv("AGENTIC_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("Set AGENTIC_TEST_DATABASE_URL to run the Postgres repository smoke test.")

    monkeypatch.setenv("AGENTIC_DATABASE_URL", database_url)
    repo = ConsoleRepository(database_url=database_url)
    repo.init_schema()
    suffix = uuid4().hex[:8]

    user = repo.create_user(
        email=f"conn-{suffix}@example.test", username=f"conn_{suffix}", password="password-1"
    )
    project = repo.create_project(
        owner_user_id=user.id, name="Conn", request_text="x", mode="ui_web_app", complexity="small"
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid=f"conn-run-{suffix}",
        run_dir=Path(tmp_path) / f"conn-run-{suffix}",
        status="running",
        mode="ui_web_app",
        reasoning="medium",
    )

    # A project-scoped connection and a run-scoped override on the same project.
    repo.create_work_system_connection(
        project_id=project.id, system="github", repository="o/project-wide"
    )
    repo.create_work_system_connection(
        project_id=project.id,
        run_id=run.id,
        system="github",
        repository="o/run-specific",
        metadata={"project_number": 5, "project_id": "PVT_x"},
    )

    by_run = repo.get_active_work_system_connection(run_id=run.id, project_id=project.id)
    assert by_run is not None
    assert by_run.repository == "o/run-specific"  # run-scoped wins
    assert by_run.metadata["project_number"] == 5

    by_project = repo.get_active_work_system_connection(project_id=project.id)
    assert by_project is not None
    assert by_project.repository == "o/project-wide"  # only the project-scoped one

    assert repo.get_active_work_system_connection(run_id=run.id, system="jira") is None
