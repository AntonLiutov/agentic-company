from pathlib import Path

from agentic_company.agents.quality import graph as quality_graph
from agentic_company.platform.db.models import AgentRunResult


def _state(tmp_path: Path) -> dict:
    return {
        "run_id": "run-1",
        "run_dir": str(tmp_path),
        "target_project_dir": str(tmp_path / "generated-project"),
        "stage": "qa",
        "status": "running",
        "repair_attempts": 0,
        "max_repair_attempts": 5,
        "blockers": [],
        "auto_confirmations": [],
        "completed_nodes": [],
    }


def _qa_pass_result() -> AgentRunResult:
    return AgentRunResult(
        agent_id="qa-codex-agent",
        status="qa_passed",
        output_artifacts=["08-qa-report-F1.md"],
        summary="QA_STATUS: passed",
    )


def test_quality_pass_marks_feature_passed_without_platform_merge(tmp_path, monkeypatch):
    result = quality_graph._apply_quality_result(
        {
            "delivery_state": _state(tmp_path),
            "run_dir": str(tmp_path),
            "work_item_id": "F1",
            "result": _qa_pass_result(),
            "status": "passed",
        }
    )

    delivery_state = result["delivery_state"]
    assert delivery_state["qa_status"] == "passed"
    assert delivery_state["status"] == "qa_feature_passed_next_feature_ready"


def test_quality_pass_does_not_require_platform_pr_record(tmp_path):
    # The platform no longer records or looks up PRs (workers own git); a QA pass
    # depends only on the QA result, never on any platform PR machinery.
    result = quality_graph._apply_quality_result(
        {
            "delivery_state": _state(tmp_path),
            "run_dir": str(tmp_path),
            "work_item_id": "F1",
            "result": _qa_pass_result(),
            "status": "passed",
        }
    )

    delivery_state = result["delivery_state"]
    assert delivery_state["qa_status"] == "passed"
    assert delivery_state["status"] == "qa_feature_passed_next_feature_ready"
    assert delivery_state["blockers"] == []
