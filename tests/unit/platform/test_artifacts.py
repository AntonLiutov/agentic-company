from pathlib import Path

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
    db_path = tmp_path / "console.db"
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    repo = ConsoleRepository(db_path)
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
