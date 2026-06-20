"""Unit tests for deterministic run finalization."""

from __future__ import annotations

import pytest

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.db.runtime_db import (
    record_generated_app_url,
    record_run_lifecycle,
)
from agentic_company.platform.run.run_finalizer import (
    RunStatus,
    is_terminal_run_status,
    resolve_run_status,
)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"status": "head_delivery_completed"}, RunStatus.COMPLETED),
        ({"status": "deployment_deployed"}, RunStatus.COMPLETED),
        ({"status": "head_planning_blocked"}, RunStatus.BLOCKED),
        ({"status": "running", "blockers": ["waiting on QA"]}, RunStatus.BLOCKED),
        ({"status": "stopped"}, RunStatus.STOPPED),
        # A run that returns without reaching a real terminal state is a failure,
        # never a silent success.
        ({"status": "in_progress"}, RunStatus.FAILED),
        ({"status": ""}, RunStatus.FAILED),
    ],
)
def test_resolve_run_status(state, expected):
    assert resolve_run_status(state) is expected


def test_blockers_outrank_a_completed_status():
    state = {"status": "head_delivery_completed", "blockers": ["deploy failed"]}
    assert resolve_run_status(state) is RunStatus.BLOCKED


def test_is_terminal_run_status():
    assert is_terminal_run_status(RunStatus.COMPLETED)
    assert is_terminal_run_status(RunStatus.STOPPED)
    assert not is_terminal_run_status(RunStatus.RUNNING)
    assert not is_terminal_run_status(RunStatus.STARTING)


def _run_uid(tmp_path) -> str:
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(email="f@example.test", username="finalizer", password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Finalizer app",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run = repo.create_run(
        project_id=project.id,
        run_uid="run-finalize",
        run_dir=run_dir,
        status=RunStatus.RUNNING,
        mode="simple_prototype",
        reasoning="medium",
    )
    return run.run_uid


def _status_of(run_uid: str) -> str:
    repo = ConsoleRepository()
    with repo.connect() as conn:
        row = conn.execute("SELECT status FROM runs WHERE run_uid = ?", (run_uid,)).fetchone()
    return str(row["status"])


def _app_url_of(run_uid: str) -> str:
    repo = ConsoleRepository()
    with repo.connect() as conn:
        row = conn.execute(
            "SELECT generated_app_url FROM runs WHERE run_uid = ?", (run_uid,)
        ).fetchone()
    return str(row["generated_app_url"])


def test_first_terminal_status_wins(tmp_path):
    run_uid = _run_uid(tmp_path)

    record_run_lifecycle(run_uid, RunStatus.STOPPED)
    # A later finalizer must not overwrite the settled stop.
    record_run_lifecycle(run_uid, RunStatus.COMPLETED)

    assert _status_of(run_uid) == RunStatus.STOPPED


def test_running_is_not_terminal_and_can_advance(tmp_path):
    run_uid = _run_uid(tmp_path)

    record_run_lifecycle(run_uid, RunStatus.RUNNING)
    record_run_lifecycle(run_uid, RunStatus.COMPLETED)

    assert _status_of(run_uid) == RunStatus.COMPLETED


def test_terminal_run_still_records_a_later_app_url(tmp_path):
    run_uid = _run_uid(tmp_path)

    record_run_lifecycle(run_uid, RunStatus.STOPPED)
    # The settled status is kept, but a deployed URL arriving later is still saved.
    record_run_lifecycle(run_uid, RunStatus.COMPLETED, generated_app_url="https://app.example")

    assert _status_of(run_uid) == RunStatus.STOPPED
    assert _app_url_of(run_uid) == "https://app.example"


def test_record_generated_app_url_does_not_touch_status(tmp_path):
    run_uid = _run_uid(tmp_path)

    record_generated_app_url(run_uid, "https://deployed.example")

    assert _status_of(run_uid) == RunStatus.RUNNING
    assert _app_url_of(run_uid) == "https://deployed.example"
