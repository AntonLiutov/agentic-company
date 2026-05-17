import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

from agentic_company.agents.quality.codex_cli import (
    QualityCodexRunner,
    build_quality_codex_prompt,
)
from agentic_company.platform.artifacts import load_execution_request


def test_quality_codex_runner_accepts_contract_artifacts(tmp_path):
    run_dir = _create_run(tmp_path)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        feature_id = "F1"
        (run_dir / "qa").mkdir(parents=True, exist_ok=True)
        (run_dir / "qa" / f"results-{feature_id}.json").write_text(
            json.dumps(
                {
                    "feature_id": feature_id,
                    "status": "passed",
                    "checks_performed": [
                        {
                            "name": "QA Codex selected evidence",
                            "status": "passed",
                            "evidence": "Executed by QA Codex.",
                        }
                    ],
                    "acceptance_criteria_coverage": [],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / f"08-qa-report-{feature_id}.md").write_text(
            "# QA Report\n\nQA_STATUS: passed\n",
            encoding="utf-8",
        )
        summary = Path(command[-2])
        summary.write_text("QA work complete.\n\nQA_STATUS: passed\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = QualityCodexRunner(command_executor=executor).run(run_dir)

    assert result.agent_id == "qa-codex-agent"
    assert result.status == "qa_passed"
    assert "08-qa-report-F1.md" in result.output_artifacts
    assert "qa/results-F1.json" in result.output_artifacts


def test_quality_codex_runner_fails_when_contract_missing(tmp_path):
    run_dir = _create_run(tmp_path)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        summary = Path(command[-2])
        summary.write_text("I forgot the required status.\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = QualityCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "qa_failed"
    payload = json.loads((run_dir / "qa" / "results-F1.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["contract_errors"]


def test_quality_codex_runner_recovers_artifacts_from_generated_project(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        feature_id = "F1"
        (target_dir / "qa").mkdir(parents=True, exist_ok=True)
        (target_dir / "qa" / f"results-{feature_id}.json").write_text(
            json.dumps(
                {
                    "feature_id": feature_id,
                    "status": "passed",
                    "checks_performed": [],
                    "acceptance_criteria_coverage": [],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        (target_dir / f"08-qa-report-{feature_id}.md").write_text(
            "# QA Report\n\nQA_STATUS: passed\n",
            encoding="utf-8",
        )
        summary = Path(command[-2])
        summary.write_text("QA work complete.\n\nQA_STATUS: passed\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = QualityCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "qa_passed"
    assert (run_dir / "08-qa-report-F1.md").exists()
    assert (run_dir / "qa" / "results-F1.json").exists()


def test_quality_codex_runner_recovers_report_from_agent_qa_folder(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    target_dir = Path(request.target_project_dir)

    def executor(command: Sequence[str], prompt: str, timeout: int, log: Path, raw: Path):
        feature_id = "F1"
        (target_dir / "qa").mkdir(parents=True, exist_ok=True)
        (target_dir / "qa" / f"results-{feature_id}.json").write_text(
            json.dumps(
                {
                    "feature_id": feature_id,
                    "status": "passed",
                    "checks_performed": [],
                    "acceptance_criteria_coverage": [],
                    "risks": [],
                }
            ),
            encoding="utf-8",
        )
        (target_dir / "qa" / f"08-qa-report-{feature_id}.md").write_text(
            "# QA Report\n\nQA_STATUS: passed\n",
            encoding="utf-8",
        )
        summary = Path(command[-2])
        summary.write_text("QA work complete.\n\nQA_STATUS: passed\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = QualityCodexRunner(command_executor=executor, contract_attempts=1).run(run_dir)

    assert result.status == "qa_passed"
    assert (run_dir / "08-qa-report-F1.md").exists()
    assert (run_dir / "qa" / "results-F1.json").exists()


def test_quality_codex_prompt_does_not_prescribe_qa_commands(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    prompt = build_quality_codex_prompt(
        request,
        run_dir,
        request.active_feature,
        attempt=1,
        previous_summary="",
    )

    assert "The platform will\nnot run a predefined QA checklist for you." in prompt
    assert "uv sync" not in prompt
    assert "compileall" not in prompt
    assert "docker compose config" not in prompt
    assert "playwright install" not in prompt


def test_quality_codex_prompt_suggests_non_exhaustive_toolbox(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    prompt = build_quality_codex_prompt(
        request,
        run_dir,
        request.active_feature,
        attempt=1,
        previous_summary="",
    )

    assert "Non-exhaustive QA toolbox" in prompt
    assert "not a complete or limiting checklist" in prompt
    assert "You may use other tools or approaches" in prompt
    assert "Playwright can be useful" in prompt
    assert "evaluate both behavior and user experience" in prompt
    assert "classify failures for Team\n  Lead routing" in prompt
    assert "usually\n  need Fullstack repair" in prompt
    assert "usually need Deployment\n  repair" in prompt
    assert "remediation_owner" in prompt
    assert "Do not run every possible tool mechanically" in prompt


def test_quality_codex_prompt_includes_exact_artifact_paths_and_repair_errors(tmp_path):
    run_dir = _create_run(tmp_path)
    request = load_execution_request(run_dir)
    prompt = build_quality_codex_prompt(
        request,
        run_dir,
        request.active_feature,
        attempt=2,
        previous_summary="QA_STATUS: passed",
        previous_contract_errors=["Missing required QA report: 08-qa-report-F1.md."],
    )

    assert str(run_dir / "08-qa-report-F1.md") in prompt
    assert str(run_dir / "qa" / "08-qa-report-F1.md") in prompt
    assert str(run_dir / "qa" / "results-F1.json") in prompt
    assert str(Path(request.target_project_dir) / "qa" / "08-qa-report-F1.md") in prompt
    assert "Missing required QA report: 08-qa-report-F1.md." in prompt


def _create_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "qa-codex"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    feature = {
        "id": "F1",
        "title": "Create and list tasks",
        "acceptance_criteria": ["API can create a task", "API can list tasks"],
        "delivery_order": 1,
    }
    request_path = run_dir / "delivery/execution-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "agent_id": "fullstack-agent",
                "agent_version": "0.1.0",
                "maturity_level": "L6 Codex Agent",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "target_project_dir": str(target_dir),
                "input_artifacts": ["05-implementation-brief.md"],
                "expected_outputs": ["README.md"],
                "instructions": ["Build the active feature."],
                "constraints": ["Keep names stable."],
                "feature_queue": [feature],
                "active_feature": feature,
                "completed_feature_ids": [],
            }
        ),
        encoding="utf-8",
    )
    return run_dir
