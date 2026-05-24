import json
from pathlib import Path

from agentic_company.platform.models import ExecutionRequest
from agentic_company.platform.quality_gates import (
    QualityCheckResult,
    QualityGateRunner,
)


class FakeBrowserAdapter:
    def __init__(self, status: str = "passed") -> None:
        self.status = status

    def check_url(self, url: str, *, run_dir: Path, work_item_id: str, gate: str):
        screenshot = run_dir / "qa" / "screenshots" / work_item_id / "fake.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(b"png")
        return [
            QualityCheckResult(
                gate=gate,
                name="fake_browser",
                status=self.status,
                evidence=f"Opened {url}",
                failure_reason="qa_failed" if self.status == "failed" else None,
                remediation_owner="fullstack-agent" if self.status == "failed" else "none",
                failure_signature="fake_browser_failed" if self.status == "failed" else "",
                evidence_refs=[],
            )
        ]


def test_quality_gate_runner_detects_placeholder_and_writes_repair(tmp_path):
    run_dir, request, feature = _create_request(tmp_path, ui=True)
    target = Path(request.target_project_dir)
    (target / "src").mkdir()
    (target / "src" / "App.tsx").write_text("<button disabled>Coming soon</button>")

    report = QualityGateRunner(browser_adapter=FakeBrowserAdapter()).run(
        run_dir,
        request,
        feature,
    )

    assert report.status == "failed"
    assert report.repair_request is not None
    assert report.repair_request.responsible_agent == "fullstack-agent"
    assert (run_dir / "10-fix-request-F1.json").exists()
    assert (run_dir / "qa" / "gates" / "F1" / "quality-gate-report.json").exists()
    registry = json.loads((run_dir / "delivery" / "artifact-registry.json").read_text())
    assert any(
        artifact["artifact_type"] == "quality_gate_report" for artifact in registry["artifacts"]
    )


def test_quality_gate_runner_allows_limited_browser_evidence_for_non_ui_task(tmp_path):
    run_dir, request, feature = _create_request(tmp_path, ui=False)

    report = QualityGateRunner().run(run_dir, request, feature)

    assert report.status == "limited"
    assert not report.blocks_release
    assert report.repair_request is None


def test_quality_gate_runner_marks_missing_browser_evidence_limited_for_ui_task(tmp_path):
    run_dir, request, feature = _create_request(tmp_path, ui=True)

    report = QualityGateRunner().run(run_dir, request, feature)

    assert report.status == "limited"
    assert report.limited_evidence
    assert report.repair_request is None


def _create_request(tmp_path: Path, *, ui: bool) -> tuple[Path, ExecutionRequest, dict]:
    run_dir = tmp_path / "run"
    target = run_dir / "generated-project"
    target.mkdir(parents=True)
    (target / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n")
    feature = {
        "id": "F1",
        "title": "Create a button UI" if ui else "Create API",
        "acceptance_criteria": ["Primary button works"] if ui else ["API returns data"],
    }
    request = ExecutionRequest(
        run_id="run-1",
        agent_id="qa-agent",
        agent_version="0.1.0",
        maturity_level="L6 Codex Agent",
        provider="codex",
        model="gpt-5.3-codex",
        target_project_dir=str(target),
        input_artifacts=[],
        expected_outputs=[],
        instructions=[],
        constraints=[],
        active_feature=feature,
    )
    return run_dir, request, feature
