from pathlib import Path

from agentic_company.platform.artifact_registry import (
    artifact_id_for,
    artifact_record_from_mapping,
    register_artifact,
)


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
