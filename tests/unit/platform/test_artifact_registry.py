from pathlib import Path

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.artifact_registry import (
    artifact_id_for,
    artifact_record_from_mapping,
    register_artifact,
)
from agentic_company.platform.runtime_db import materialize_planning_items, record_artifact_link
from agentic_company.platform.tool_contracts import ArtifactRegistrationRequest


def test_artifact_id_is_stable():
    assert artifact_id_for("run-1", "reports/summary.md") == artifact_id_for(
        "run-1", "reports\\summary.md"
    )


def test_register_artifact_requires_explicit_metadata(tmp_path: Path):
    report = tmp_path / "handoff" / "project" / "final" / "release-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<h1>Report</h1>", encoding="utf-8")

    record = register_artifact(
        tmp_path,
        artifact_id=artifact_id_for("run-1", "handoff/project/final/release-report.html"),
        relative_path="handoff/project/final/release-report.html",
        run_id="run-1",
        owner_agent="documentation-handoff-agent",
        label="Final report",
        visibility="release",
        artifact_type="release_report",
        source_tool="run_handoff",
        external_refs=[{"system": "github", "type": "issue", "id": "1", "url": "https://x"}],
    )

    assert record.artifact_id == artifact_id_for(
        "run-1", "handoff/project/final/release-report.html"
    )
    assert record.label == "Final report"
    assert not (tmp_path / "delivery" / "artifact-registry.json").exists()


def test_artifact_record_from_mapping_requires_explicit_metadata():
    payload = {
        "artifact_id": "art_1",
        "relative_path": "08-qa-report-F1.md",
        "run_id": "run-1",
        "owner_agent": "qa-agent",
        "visibility": "qa_evidence",
        "source_tool": "run_qa",
    }

    try:
        artifact_record_from_mapping(payload)
    except ValueError as exc:
        assert "artifact_type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing artifact_type must fail")


def test_db_artifact_registration_requires_existing_run_file(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "console.db"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv("AGENTIC_CONSOLE_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENTIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = ConsoleRepository(db_path)
    repo.init_schema()
    user = repo.create_user(
        email="artifact@example.test",
        username="artifact-user",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Artifacts",
        request_text="Artifacts",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    run = repo.create_run(
        project_id=project.id,
        run_uid="run",
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
    materialize_planning_items("run")

    try:
        record_artifact_link(
            run_dir,
            ArtifactRegistrationRequest(
                artifact_id=artifact_id_for("run", "missing-report.md"),
                artifact_type="execution_summary",
                visibility="business",
                owner_agent="business-analyst-agent",
                source_tool="codex_exec",
                label="Missing report",
                relative_path="missing-report.md",
                run_id="run",
                work_item_id="PLAN-01",
                task_scoped=True,
            ),
        )
    except ValueError as exc:
        assert "existing run-local file" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing artifact file must fail DB registration")

    assert repo.list_artifact_records(run.id) == []
