from pathlib import Path

from agentic_company.platform.artifact_registry import (
    ARTIFACT_REGISTRY_PATH,
    artifact_id_for,
    get_artifact_by_id,
    list_artifacts,
    load_artifact_registry,
    register_artifact,
    register_artifacts_from_refs,
)
from agentic_company.platform.artifacts import artifact_ref


def test_artifact_id_is_deterministic():
    assert artifact_id_for("run-1", "reports/summary.md") == artifact_id_for(
        "run-1", "reports\\summary.md"
    )


def test_register_artifact_roundtrip_and_upsert(tmp_path: Path):
    report = tmp_path / "handoff" / "project" / "final" / "release-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<h1>Report</h1>", encoding="utf-8")

    first = register_artifact(
        tmp_path,
        relative_path="handoff/project/final/release-report.html",
        run_id="run-1",
        owner_agent="documentation-handoff-agent",
        label="Final report",
        visibility="release",
        artifact_type="release_report",
        external_refs=[{"system": "github", "type": "issue", "id": "1", "url": "https://x"}],
    )
    second = register_artifact(
        tmp_path,
        relative_path="handoff/project/final/release-report.html",
        run_id="run-1",
        owner_agent="documentation-handoff-agent",
        label="Updated final report",
        visibility="release",
        artifact_type="release_report",
    )

    records = load_artifact_registry(tmp_path)

    assert (tmp_path / ARTIFACT_REGISTRY_PATH).exists()
    assert first.artifact_id == second.artifact_id
    assert len(records) == 1
    assert records[0].label == "Updated final report"
    assert get_artifact_by_id(tmp_path, first.artifact_id) == records[0]


def test_register_artifacts_from_refs_maps_legacy_values(tmp_path: Path):
    report = tmp_path / "08-qa-report-F1.md"
    report.write_text("# QA\n", encoding="utf-8")

    records = register_artifacts_from_refs(
        tmp_path,
        [
            artifact_ref(
                "08-qa-report-F1.md",
                kind="qa",
                owner_agent="qa-agent",
            )
        ],
        run_id="run-1",
        source_tool="qa",
    )

    assert records[0].artifact_type == "qa_report"
    assert records[0].visibility == "qa_evidence"
    qa_artifacts = list_artifacts(tmp_path, visibility="qa_evidence")
    assert qa_artifacts[0].relative_path == "08-qa-report-F1.md"
