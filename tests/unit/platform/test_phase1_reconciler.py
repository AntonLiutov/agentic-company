from __future__ import annotations

from pathlib import Path

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.runtime_db import (
    artifact_links_for_paths,
    build_run_reconcile_snapshot,
    reconcile_run,
    record_artifact_link,
    request_run_control_intent,
    run_stop_requested,
)
from agentic_company.platform.status_snapshot import (
    build_delivery_status_snapshot,
    build_sprint_status_snapshot,
)
from agentic_company.platform.tool_contracts import ArtifactRegistrationRequest


def test_control_intent_cancel_is_durable_stop_and_reconciles_active_items(tmp_path):
    repo, run, run_dir = _setup_run(tmp_path)
    _insert_work_item(repo, run.id, "F1", status="in_progress", lane="in_progress", active=1)

    request_run_control_intent("phase1-run", "cancel", "Operator stopped the run.")

    assert run_stop_requested("phase1-run", run_dir) is True
    snapshot = build_run_reconcile_snapshot("phase1-run")
    assert snapshot.control_intent == "cancel"
    assert snapshot.active_items == 1

    result = reconcile_run("phase1-run")

    assert result.applied is True
    assert result.action == "cancel"
    assert repo.get_run(run.id).status == "stopped"
    work_items = {item.work_item_id: item for item in repo.list_work_items(run.id)}
    assert work_items["F1"].status == "blocked"
    assert work_items["F1"].active is False
    assert "Operator stopped the run." in work_items["F1"].blocker


def test_cancel_reconciler_does_not_demote_completed_run(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    _insert_work_item(repo, run.id, "F1", status="in_progress", lane="in_progress", active=1)
    repo.update_run_status(run.id, "completed")

    request_run_control_intent("phase1-run", "cancel", "Late stop click.")
    result = reconcile_run("phase1-run")

    assert result.applied is True
    assert result.action == "cancel_ignored_terminal"
    assert repo.get_run(run.id).status == "completed"
    work_items = {item.work_item_id: item for item in repo.list_work_items(run.id)}
    assert work_items["F1"].status == "in_progress"
    assert work_items["F1"].active is True
    assert build_run_reconcile_snapshot("phase1-run").control_intent == ""


def test_reconciler_finalizes_completed_db_world_without_llm_status(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    _insert_work_item(repo, run.id, "F1", status="done", lane="done", active=0)
    repo.upsert_sprint(
        run.id,
        sprint_id="sprint-01",
        title="Sprint 1",
        delivery_order=1,
        status="done",
        is_final=True,
    )

    result = reconcile_run("phase1-run")

    assert result.applied is True
    assert result.action == "finalize_completed"
    assert repo.get_run(run.id).status == "completed"


def test_reconciler_does_not_finalize_empty_sprint(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    repo.upsert_sprint(
        run.id,
        sprint_id="sprint-01",
        title="Sprint 1",
        delivery_order=1,
        status="done",
        is_final=True,
    )

    result = reconcile_run("phase1-run")

    assert result.action == "noop"
    assert repo.get_run(run.id).status == "running"


def test_reconciler_does_not_finalize_with_open_orphan_delivery_item(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    repo.upsert_sprint(
        run.id,
        sprint_id="sprint-01",
        title="Sprint 1",
        delivery_order=1,
        status="done",
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
            VALUES (?, 'ORPHAN-1', 'Orphan', 'sprint-missing', 99, 'review',
                    'review', 'fullstack-agent', 'fullstack-agent', 0, '[]',
                    '[]', '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (run.id,),
        )

    result = reconcile_run("phase1-run")

    assert result.action == "noop"
    assert repo.get_run(run.id).status == "running"


def test_artifact_refs_accept_registered_artifact_ids_without_path_guessing(tmp_path):
    _repo, _run, run_dir = _setup_run(tmp_path)
    report = run_dir / "reports" / "summary.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Summary", encoding="utf-8")
    record = record_artifact_link(
        run_dir,
        ArtifactRegistrationRequest(
            run_id="phase1-run",
            artifact_id="art_registered_summary",
            artifact_type="status_snapshot",
            visibility="developer",
            owner_agent="runtime-reconciler",
            source_tool="reconcile_run",
            label="Runtime summary",
            relative_path="reports/summary.md",
            work_item_id="PLAN-04",
        ),
    )

    refs = artifact_links_for_paths("phase1-run", [record.artifact_id])

    assert len(refs) == 1
    assert refs[0].artifact_id == "art_registered_summary"
    assert refs[0].path == "reports/summary.md"


def test_deterministic_status_snapshots_read_db_state(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    _insert_work_item(repo, run.id, "F1", status="done", lane="done", active=0)
    repo.upsert_sprint(
        run.id,
        sprint_id="sprint-01",
        title="Sprint 1",
        delivery_order=1,
        status="done",
        is_final=True,
    )
    state = {
        "run_id": "phase1-run",
        "run_dir": str(tmp_path / "phase1-run"),
        "stage": "team_lead",
        "status": "running",
        "blockers": [],
    }

    sprint = build_sprint_status_snapshot(state, sprint_id="sprint-01")
    delivery = build_delivery_status_snapshot(state)

    assert sprint["can_complete_sprint"] is True
    assert sprint["completed_work_item_ids"] == ["F1"]
    assert delivery["can_complete_delivery"] is True
    assert delivery["delivery_status"] == "ready_to_complete"


def test_external_work_refs_are_idempotent_phase1_board_contract(tmp_path):
    repo, run, _run_dir = _setup_run(tmp_path)
    connection_id = repo.create_work_system_connection(
        project_id=run.project_id,
        run_id=run.id,
        system="github",
        name="GitHub",
        repository="AntonLiutov/agentic-company",
        default_branch="main",
        risk_mode="assisted",
    )

    first = repo.upsert_external_work_ref(
        run.id,
        work_item_id="PLAN-04",
        connection_id=connection_id,
        system="github",
        external_type="issue",
        idempotency_key="PLAN-04-primary-issue",
        sync_status="pending",
    )
    second = repo.upsert_external_work_ref(
        run.id,
        work_item_id="PLAN-04",
        connection_id=connection_id,
        system="github",
        external_type="issue",
        idempotency_key="PLAN-04-primary-issue",
        external_id="123",
        external_url="https://github.com/AntonLiutov/agentic-company/issues/123",
        sync_status="synced",
    )

    refs = repo.list_external_work_refs(run.id, work_item_id="PLAN-04", system="github")
    assert first.id == second.id
    assert len(refs) == 1
    assert refs[0].sync_status == "synced"
    assert refs[0].connection_id == connection_id
    assert refs[0].external_id == "123"
    assert refs[0].idempotency_key == "PLAN-04-primary-issue"


def _setup_run(tmp_path: Path):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email="phase1@example.test",
        username="phase1",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Phase 1",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
        status="running",
    )
    run_dir = tmp_path / "phase1-run"
    run_dir.mkdir()
    (run_dir / "00-requirements.md").write_text("Build", encoding="utf-8")
    run = repo.create_run(
        project_id=project.id,
        run_uid="phase1-run",
        run_dir=run_dir,
        status="running",
        mode="simple_prototype",
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
    return repo, run, run_dir


def _insert_work_item(
    repo: ConsoleRepository,
    run_id: int,
    work_item_id: str,
    *,
    status: str,
    lane: str,
    active: int,
) -> None:
    with repo.connect() as conn:
        conn.execute(
            """
            INSERT INTO work_items (
                run_id, work_item_id, title, sprint_id, delivery_order, status,
                lane, owner_agent, assigned_agent, active, source_refs,
                artifact_ids, blocker, created_at, updated_at
            )
            VALUES (?, ?, ?, 'sprint-01', 1, ?, ?, 'fullstack-agent',
                    'fullstack-agent', ?, '[]', '[]', '', '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
            ON CONFLICT(run_id, work_item_id) DO UPDATE SET
                status = excluded.status,
                lane = excluded.lane,
                active = excluded.active,
                updated_at = excluded.updated_at
            """,
            (run_id, work_item_id, work_item_id, status, lane, active),
        )
