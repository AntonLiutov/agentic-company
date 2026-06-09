from pathlib import Path

import pytest

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifacts import (
    build_execution_request_payload,
    canonical_output_artifact_refs,
    discover_implementation_artifacts,
    load_execution_request,
    read_json_artifact,
    read_text_artifact,
    update_execution_request_context,
    write_execution_request,
)


def test_update_execution_request_context_clears_stale_codex_resume_thread(
    tmp_path: Path,
    monkeypatch,
):
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(email="runner@example.test", username="runner", password="password-1")
    project = repo.create_project(
        owner_user_id=user.id,
        name="Run",
        request_text="Build",
        mode="simple_prototype",
        complexity="simple",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="simple_prototype",
        reasoning="medium",
    )
    write_execution_request(
        run_dir,
        {
            "run_id": "run",
            "agent_id": "fullstack-agent",
            "agent_version": "0.1.0",
            "maturity_level": "L6 Codex Agent",
            "provider": "codex",
            "model": "gpt-5.5",
            "target_project_dir": str(run_dir / "generated-project"),
            "input_artifacts": [],
            "expected_outputs": [],
            "instructions": [],
            "constraints": [],
            "codex_resume_thread_id": "thread-from-previous-agent",
        },
    )

    update_execution_request_context(run_dir)

    request = load_execution_request(run_dir)
    assert request.codex_resume_thread_id == ""


def test_build_execution_request_payload_omits_retired_topology_label(tmp_path: Path):
    payload = build_execution_request_payload(
        {
            "run_id": "run",
            "run_dir": str(tmp_path / "run"),
        },
        agent_id="deployment-agent",
        model="gpt-5.5",
        input_artifacts=[],
        expected_outputs=[],
        instructions=[],
        constraints=[],
        target_project_dir=str(tmp_path / "run" / "generated-project"),
    )

    assert set(payload) == {
        "agent_id",
        "agent_version",
        "codex_resume_thread_id",
        "completed_work_item_ids",
        "constraints",
        "execution_id",
        "execution_intent",
        "expected_outputs",
        "input_artifacts",
        "instructions",
        "maturity_level",
        "model",
        "parent_message_id",
        "provider",
        "run_id",
        "target_project_dir",
        "work_item",
    }


def test_build_execution_request_payload_requires_target_project_dir():
    with pytest.raises(ValueError, match="explicit target_project_dir"):
        build_execution_request_payload(
            {"run_id": "run"},
            agent_id="fullstack-agent",
            model="gpt-5.5",
            input_artifacts=[],
            expected_outputs=[],
            instructions=[],
            constraints=[],
            target_project_dir="",
        )


def test_build_execution_request_payload_includes_explicit_handoff_contract(tmp_path: Path):
    payload = build_execution_request_payload(
        {
            "run_id": "run",
            "agent_execution_id": "exec-1",
            "agent_execution_intent": "handoff",
            "agent_call_message_id": "msg-1",
        },
        agent_id="documentation-handoff-agent",
        model="gpt-5.5",
        input_artifacts=["qa/report.md"],
        expected_outputs=["handoff/report.html"],
        instructions=["write report"],
        constraints=["db contract only"],
        target_project_dir=str(tmp_path / "run" / "generated-project"),
        work_item={"id": "PLAN-04"},
        completed_work_item_ids=["F1"],
        codex_resume_thread_id="thread-1",
        handoff_scope="sprint",
        handoff_sprint_id="sprint-01",
        handoff_output_dir="handoff/sprint-01",
        handoff_expected_outputs=["release-report.html"],
    )

    assert payload["execution_id"] == "exec-1"
    assert payload["execution_intent"] == "handoff"
    assert payload["parent_message_id"] == "msg-1"
    assert payload["work_item"] == {"id": "PLAN-04"}
    assert payload["completed_work_item_ids"] == ["F1"]
    assert payload["handoff_scope"] == "sprint"
    assert payload["handoff_sprint_id"] == "sprint-01"
    assert payload["handoff_output_dir"] == "handoff/sprint-01"
    assert payload["handoff_expected_outputs"] == ["release-report.html"]


def test_update_execution_request_context_updates_db_contract(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    payload = {
        "run_id": "run-1",
        "agent_id": "fullstack-agent",
        "agent_version": "0.1.0",
        "maturity_level": "L6 Codex Agent",
        "provider": "codex",
        "model": "gpt-5.5",
        "target_project_dir": str(run_dir / "generated-project"),
        "input_artifacts": [],
        "expected_outputs": [],
        "instructions": [],
        "constraints": [],
    }
    writes = []

    monkeypatch.setattr(
        "agentic_company.platform.runtime_db.latest_execution_request",
        lambda run_id: dict(payload),
    )
    monkeypatch.setattr(
        "agentic_company.platform.runtime_db.record_execution_request",
        lambda run_id, updated: writes.append((run_id, dict(updated))),
    )

    update_execution_request_context(
        run_dir,
        execution_id="exec-2",
        execution_intent="repair",
        parent_message_id="msg-2",
        codex_resume_thread_id="thread-2",
        work_item={"id": "F1"},
        completed_work_item_ids=["PLAN-01"],
        handoff_scope="release",
        handoff_sprint_id="sprint-01",
        handoff_output_dir="handoff/release",
        handoff_expected_outputs=["release-report.html"],
    )

    assert writes[0][0] == "run-1"
    updated = writes[0][1]
    assert updated["execution_id"] == "exec-2"
    assert updated["execution_intent"] == "repair"
    assert updated["parent_message_id"] == "msg-2"
    assert updated["codex_resume_thread_id"] == "thread-2"
    assert updated["work_item"] == {"id": "F1"}
    assert updated["completed_work_item_ids"] == ["PLAN-01"]
    assert updated["handoff_scope"] == "release"
    assert updated["handoff_sprint_id"] == "sprint-01"
    assert updated["handoff_output_dir"] == "handoff/release"
    assert updated["handoff_expected_outputs"] == ["release-report.html"]


def test_read_json_artifact_tolerates_and_normalizes_utf8_bom(tmp_path: Path):
    path = tmp_path / "artifact.json"
    path.write_text('{"ok": true}', encoding="utf-8-sig")

    payload = read_json_artifact(path, normalize_bom=True)

    assert payload == {"ok": True}
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_read_text_artifact_tolerates_windows_encoded_text(tmp_path: Path):
    path = tmp_path / "summary.md"
    path.write_bytes(b"HANDOFF_STATUS: ready\n\nSprint \x96 done\n")

    text = read_text_artifact(path)

    assert "HANDOFF_STATUS: ready" in text
    assert "Sprint - done" not in text
    assert "Sprint \u2013 done" in text


def test_canonical_output_artifact_refs_converts_target_relative_files_to_run_refs(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    target_project_dir.mkdir(parents=True)
    (target_project_dir / "README.md").write_text("# App\n", encoding="utf-8")

    refs = canonical_output_artifact_refs(
        run_dir=run_dir,
        target_project_dir=target_project_dir,
        artifact_refs=["README.md"],
    )

    assert refs == ["generated-project/README.md"]


def test_canonical_output_artifact_refs_preserves_existing_run_relative_files(
    tmp_path: Path,
):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    report = run_dir / "codex" / "F1" / "summary.md"
    report.parent.mkdir(parents=True)
    target_project_dir.mkdir(parents=True)
    report.write_text("# Summary\n", encoding="utf-8")

    refs = canonical_output_artifact_refs(
        run_dir=run_dir,
        target_project_dir=target_project_dir,
        artifact_refs=["codex/F1/summary.md"],
    )

    assert refs == ["codex/F1/summary.md"]


def test_canonical_output_artifact_refs_rejects_paths_outside_run(tmp_path: Path):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    outside = tmp_path / "outside.md"
    target_project_dir.mkdir(parents=True)
    outside.write_text("# Nope\n", encoding="utf-8")

    try:
        canonical_output_artifact_refs(
            run_dir=run_dir,
            target_project_dir=target_project_dir,
            artifact_refs=[str(outside)],
        )
    except ValueError as exc:
        assert "inside run directory" in str(exc)
    else:
        raise AssertionError("Expected outside artifact path to fail")


def test_discover_implementation_artifacts_uses_explicit_target_root(tmp_path: Path):
    run_dir = tmp_path / "run"
    target_project_dir = run_dir / "generated-project"
    web_dir = target_project_dir / "web"
    cache_dir = target_project_dir / "node_modules" / "dep"
    web_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    (web_dir / "index.html").write_text("<main>App</main>\n", encoding="utf-8")
    (web_dir / "app.js").write_text("console.log('app')\n", encoding="utf-8")
    (target_project_dir / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (target_project_dir / "uv.lock").write_text("lock\n", encoding="utf-8")
    (cache_dir / "index.js").write_text("cached\n", encoding="utf-8")

    refs = discover_implementation_artifacts(
        run_dir=run_dir,
        target_project_dir=target_project_dir,
    )

    assert refs == [
        "generated-project/web/app.js",
        "generated-project/web/index.html",
    ]


def test_discover_implementation_artifacts_rejects_target_outside_run(tmp_path: Path):
    run_dir = tmp_path / "run"
    outside = tmp_path / "outside-project"
    run_dir.mkdir()
    outside.mkdir()

    try:
        discover_implementation_artifacts(run_dir=run_dir, target_project_dir=outside)
    except ValueError as exc:
        assert "inside run directory" in str(exc)
    else:
        raise AssertionError("Expected outside target project to fail")
