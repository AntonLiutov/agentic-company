from pathlib import Path

from agentic_company.agents.quality import graph as quality_graph
from agentic_company.platform.delivery.delivery_pr import WorkItemPrMergeResult
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


def test_quality_pass_merges_recorded_pr_before_marking_feature_passed(tmp_path, monkeypatch):
    calls = []

    def merge(run_id: str, work_item_id: str):
        calls.append((run_id, work_item_id))
        return WorkItemPrMergeResult(
            ok=True,
            status="merged",
            message="merged",
            pr_url="https://github.com/o/app/pull/7",
        )

    monkeypatch.setattr(quality_graph, "_merge_work_item_pr_after_qa_pass", merge)

    result = quality_graph._apply_quality_result(
        {
            "delivery_state": _state(tmp_path),
            "run_dir": str(tmp_path),
            "work_item_id": "F1",
            "result": _qa_pass_result(),
            "status": "passed",
        }
    )

    assert calls == [("run-1", "F1")]
    delivery_state = result["delivery_state"]
    assert delivery_state["qa_status"] == "passed"
    assert delivery_state["status"] == "qa_feature_passed_next_feature_ready"


def test_quality_pass_becomes_blocked_when_required_pr_merge_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        quality_graph,
        "_merge_work_item_pr_after_qa_pass",
        lambda run_id, work_item_id: WorkItemPrMergeResult(
            ok=False,
            status="failed",
            message="PR merge after QA pass failed for work item F1: boom",
        ),
    )

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
    assert delivery_state["qa_status"] == "failed"
    assert delivery_state["status"] == "qa_pr_merge_blocked"
    assert delivery_state["blockers"] == [
        "PR merge after QA pass failed for work item F1: boom"
    ]
