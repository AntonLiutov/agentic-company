import json
from pathlib import Path

from agentic_company.agents.quality.agent import QualityAgent
from agentic_company.agents.quality.graph import run_quality_agent_graph
from agentic_company.platform.agent_runtime import DirectSpecialistAgentExecutor
from agentic_company.platform.models import AgentRunResult
from agentic_company.platform.state import DeliveryState, initial_delivery_state


def test_feature_qa_marks_active_feature_passed_and_selects_next(tmp_path):
    run_dir, state = _create_feature_run(tmp_path)

    result = run_quality_agent_graph(
        state,
        runner=FakeQaRunner("qa_passed"),
        agent_executor=DirectSpecialistAgentExecutor(),
    )

    assert result["stage"] == "qa"
    assert result["status"] == "qa_feature_passed_next_feature_ready"
    assert result["qa_status"] == "passed"
    assert result["completed_feature_ids"] == ["F1"]
    assert result["active_feature_id"] == "F2"
    assert result["feature_statuses"] == {"F1": "qa_passed"}
    assert "08-qa-report-F1.md" in [artifact["path"] for artifact in result["artifacts"]]
    assert _event_names(run_dir).count("qa_completed") == 1


def test_quality_agent_routes_to_codex_owned_graph(tmp_path):
    _run_dir, state = _create_feature_run(tmp_path)

    result = QualityAgent(
        runner=FakeQaRunner("qa_passed"),
        agent_executor=DirectSpecialistAgentExecutor(),
    ).run(state)

    assert result["stage"] == "qa"
    assert result["active_feature_id"] == "F2"


def test_feature_qa_marks_deployment_ready_after_final_feature_passes(tmp_path):
    _run_dir, state = _create_feature_run(tmp_path)
    state["active_feature_id"] = "F2"
    state["completed_feature_ids"] = ["F1"]
    state["feature_statuses"] = {"F1": "qa_passed"}

    result = run_quality_agent_graph(
        state,
        runner=FakeQaRunner("qa_passed"),
        agent_executor=DirectSpecialistAgentExecutor(),
    )

    assert result["status"] == "feature_queue_qa_completed_deployment_ready"
    assert result["completed_feature_ids"] == ["F1", "F2"]
    assert result["active_feature_id"] is None
    assert result["feature_statuses"] == {"F1": "qa_passed", "F2": "qa_passed"}
    assert result["blockers"] == []


def test_feature_qa_requests_repair_within_active_feature_scope(tmp_path):
    _run_dir, state = _create_feature_run(tmp_path)

    result = run_quality_agent_graph(
        state,
        runner=FakeQaRunner("qa_failed"),
        agent_executor=DirectSpecialistAgentExecutor(),
    )

    assert result["status"] == "qa_feature_failed_repair_ready"
    assert result["active_feature_id"] == "F1"
    assert result["completed_feature_ids"] == []
    assert result["feature_statuses"] == {"F1": "qa_failed"}
    assert result["feature_repair_attempts"] == {"F1": 1}
    assert result["blockers"] == []


def test_feature_qa_blocks_after_max_repairs(tmp_path):
    _run_dir, state = _create_feature_run(tmp_path)
    state["feature_repair_attempts"] = {"F1": 4}

    result = run_quality_agent_graph(
        state,
        runner=FakeQaRunner("qa_failed"),
        agent_executor=DirectSpecialistAgentExecutor(),
    )

    assert result["status"] == "qa_feature_failed_blocked"
    assert result["feature_repair_attempts"] == {"F1": 5}
    assert result["blockers"] == ["QA failed feature F1 after 5 attempts."]


def test_feature_qa_blocks_after_repeated_failure_signature(tmp_path):
    _run_dir, state = _create_feature_run(tmp_path)
    state["feature_failure_signatures"] = {"F1": ["button_dead"]}

    result = run_quality_agent_graph(
        state,
        runner=FakeQaRunner("qa_failed", failure_signature="button_dead"),
        agent_executor=DirectSpecialistAgentExecutor(),
    )

    assert result["status"] == "qa_feature_failed_blocked"
    assert result["feature_repair_attempts"] == {"F1": 1}
    assert result["feature_failure_signatures"] == {"F1": ["button_dead", "button_dead"]}
    assert "button_dead" in result["blockers"][0]


def _create_feature_run(tmp_path: Path) -> tuple[Path, DeliveryState]:
    run_dir = tmp_path / "runs" / "feature-qa"
    target_dir = run_dir / "generated-project"
    target_dir.mkdir(parents=True)
    feature_queue = [
        {
            "id": "F1",
            "title": "Create and list tasks",
            "acceptance_criteria": ["API can create a task", "API can list tasks"],
            "delivery_order": 1,
        },
        {
            "id": "F2",
            "title": "Mark tasks done",
            "acceptance_criteria": ["API can mark a task as done"],
            "delivery_order": 2,
        },
    ]
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
                "feature_queue": feature_queue,
                "active_feature": feature_queue[0],
                "completed_feature_ids": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    state = initial_delivery_state(run_id=run_dir.name, run_dir=run_dir)
    state["feature_queue"] = feature_queue
    state["active_feature_id"] = "F1"
    return run_dir, state


class FakeQaRunner:
    def __init__(self, status: str, *, failure_signature: str = "") -> None:
        self.status = status
        self.failure_signature = failure_signature

    def run(self, run_dir: Path) -> AgentRunResult:
        if self.status == "qa_failed" and self.failure_signature:
            (run_dir / "10-fix-request-F1.json").write_text(
                json.dumps(
                    {
                        "failure_signature": self.failure_signature,
                        "blocking_findings": [{"summary": "Repeated failure."}],
                    }
                ),
                encoding="utf-8",
            )
        return AgentRunResult(
            agent_id="qa-codex-agent",
            status=self.status,
            output_artifacts=["08-qa-report-F1.md", "qa/results-F1.json"],
            summary=f"QA_STATUS: {self.status.removeprefix('qa_')}",
        )


def _event_names(run_dir: Path) -> list[str]:
    return [
        json.loads(line)["event"]
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
