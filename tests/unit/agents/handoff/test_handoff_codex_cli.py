import json
import subprocess
from pathlib import Path

import pytest

from agentic_company.agents.handoff.codex_cli import (
    HANDOFF_EVIDENCE_JSON,
    HANDOFF_REPORT_HTML,
    HANDOFF_SUMMARY_MARKDOWN,
    HandoffCodexRunner,
    build_handoff_codex_prompt,
    handoff_contract_paths,
    read_handoff_contract,
)
from agentic_company.agents.handoff.contracts import FINAL_PROJECT_REPORT_SCOPE
from agentic_company.platform.messages import AgentMessage, AgentMessageStore
from agentic_company.platform.models import ExecutionRequest


def test_handoff_codex_runner_accepts_contract_artifacts(tmp_path):
    run_dir = tmp_path / "runs" / "handoff-codex"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir(parents=True)
    target_dir.mkdir()
    _write_execution_request(run_dir, target_dir)
    paths = handoff_contract_paths(_execution_request(run_dir, target_dir), run_dir)

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        assert "Handoff Codex Agent" in prompt
        assert "platform will not render a predefined report template" in prompt
        assert timeout_seconds == 1800
        (run_dir / paths.summary).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / paths.summary).write_text("# Release\n", encoding="utf-8")
        (run_dir / paths.html).write_text("<html>ready</html>\n", encoding="utf-8")
        (run_dir / paths.evidence).write_text(
            json.dumps({"status": "ready"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="HANDOFF_STATUS: ready\n", stderr="")

    result = HandoffCodexRunner(command_executor=executor).run(run_dir)

    assert result.agent_id == "handoff-codex-agent"
    assert result.status == "handoff_ready"
    assert paths.summary in result.output_artifacts
    assert paths.html in result.output_artifacts
    assert paths.evidence in result.output_artifacts


def test_handoff_codex_runner_accepts_windows_encoded_summary(tmp_path):
    run_dir = tmp_path / "runs" / "handoff-codex"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir(parents=True)
    target_dir.mkdir()
    _write_execution_request(run_dir, target_dir)
    paths = handoff_contract_paths(_execution_request(run_dir, target_dir), run_dir)

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        summary_path = next(Path(arg) for arg in command if str(arg).endswith("summary.md"))
        summary_path.write_bytes(b"HANDOFF_STATUS: ready\n\nSprint \x96 handoff ready.\n")
        (run_dir / paths.summary).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / paths.summary).write_text("# Release\n", encoding="utf-8")
        (run_dir / paths.html).write_text("<html>ready</html>\n", encoding="utf-8")
        (run_dir / paths.evidence).write_text(
            json.dumps({"status": "ready"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = HandoffCodexRunner(command_executor=executor).run(run_dir)

    assert result.status == "handoff_ready"
    assert "Sprint \u2013 handoff ready." in result.summary


def test_handoff_codex_runner_recovers_contract_from_generated_project(tmp_path):
    run_dir = tmp_path / "runs" / "handoff-fallback"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir(parents=True)
    target_dir.mkdir()
    _write_execution_request(run_dir, target_dir)
    paths = handoff_contract_paths(_execution_request(run_dir, target_dir), run_dir)

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        (target_dir / paths.summary).parent.mkdir(parents=True)
        (target_dir / paths.summary).write_text("# Release\n", encoding="utf-8")
        (target_dir / paths.html).write_text("<html>ready</html>\n", encoding="utf-8")
        (target_dir / paths.evidence).write_text(
            json.dumps({"status": "ready"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="HANDOFF_STATUS: ready\n", stderr="")

    result = HandoffCodexRunner(command_executor=executor).run(run_dir)

    assert result.status == "handoff_ready"
    assert (run_dir / paths.summary).exists()
    assert (run_dir / paths.html).exists()
    assert (run_dir / paths.evidence).exists()


def test_handoff_codex_prompt_is_agent_owned_and_non_exhaustive(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    request = _execution_request(run_dir, target_dir)
    paths = handoff_contract_paths(request, run_dir)

    prompt = build_handoff_codex_prompt(request, run_dir, attempt=1, previous_summary="")

    assert "sole owner of client-facing release communication" in prompt
    assert "will not render a predefined report template" in prompt
    assert "client-facing release communication" in prompt
    assert "Decide what the recipient of the handoff needs to know" in prompt
    assert "Do not follow a fixed section template" in prompt
    assert "client-facing HTML report" in prompt
    assert "structured JSON evidence manifest" in prompt
    assert "Keep technical details proportional" in prompt
    assert "local run artifacts are the source of truth" in prompt
    assert str(run_dir / paths.summary) in prompt
    assert str(run_dir / paths.html) in prompt
    assert str(run_dir / paths.evidence) in prompt


def test_handoff_codex_prompt_includes_upstream_agent_message(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir()
    request = _execution_request(run_dir, target_dir)
    AgentMessageStore(run_dir).append(
        AgentMessage(
            message_id="msg-review",
            from_agent="team-lead-agent",
            to_agent="documentation-handoff-agent",
            intent="request_handoff",
            content="Create the release report for the reviewed sprint scope.",
            artifact_refs=["handoff/release-report.html"],
            correlation_id="sprint-01",
        )
    )

    prompt = build_handoff_codex_prompt(request, run_dir, attempt=1, previous_summary="")

    assert "Upstream agent messages" in prompt
    assert "msg-review" in prompt
    assert "request_handoff" in prompt
    assert "Create the release report for the reviewed sprint scope." in prompt


def test_handoff_contract_paths_use_explicit_final_project_scope(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir()
    request = _execution_request(
        run_dir,
        target_dir,
        handoff_scope=FINAL_PROJECT_REPORT_SCOPE,
        handoff_sprint_id="",
    )

    paths = handoff_contract_paths(request, run_dir)

    assert paths.summary == "handoff/project/final/09-handoff-summary.md"
    assert paths.html == "handoff/project/final/release-report.html"
    assert paths.evidence == "handoff/project/final/release-evidence.json"


def test_handoff_contract_paths_keep_explicit_sprint_scope_over_future_project_note(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir()
    request = _execution_request(run_dir, target_dir)
    AgentMessageStore(run_dir).append(
        AgentMessage(
            message_id="msg-sprint",
            from_agent="team-lead-agent",
            to_agent="documentation-handoff-agent",
            intent="request_handoff",
            content=(
                "Create a sprint-scoped handoff now. After this is accepted, "
                "we may request a separate project/final handoff."
            ),
            correlation_id="sprint-01",
        )
    )
    request.parent_message_id = "msg-sprint"

    paths = handoff_contract_paths(request, run_dir)

    assert paths.summary == "handoff/sprints/sprint-01/09-handoff-summary.md"
    assert paths.html == "handoff/sprints/sprint-01/release-report.html"
    assert paths.evidence == "handoff/sprints/sprint-01/release-evidence.json"


def test_handoff_contract_paths_require_explicit_scope(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    request = _execution_request(run_dir, target_dir, handoff_scope="", handoff_sprint_id="")

    with pytest.raises(ValueError, match="handoff_scope"):
        handoff_contract_paths(request, run_dir)


def test_handoff_contract_accepts_artifacts_without_status_line(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    (run_dir / "handoff").mkdir(parents=True)
    target_dir.mkdir(parents=True)
    (run_dir / HANDOFF_SUMMARY_MARKDOWN).write_text("# Release\n", encoding="utf-8")
    (run_dir / HANDOFF_REPORT_HTML).write_text("<html>ready</html>\n", encoding="utf-8")
    (run_dir / HANDOFF_EVIDENCE_JSON).write_text(
        json.dumps({"status": "ready"}) + "\n",
        encoding="utf-8",
    )

    contract = read_handoff_contract(run_dir, target_dir, "summary without status")

    assert contract["contract_valid"]
    assert contract["status"] == "ready"


def _write_execution_request(run_dir: Path, target_dir: Path) -> None:
    request_path = run_dir / "delivery/execution-request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    request_path.write_text(
        json.dumps(_execution_request(run_dir, target_dir).to_dict()),
        encoding="utf-8",
    )


def _execution_request(
    run_dir: Path,
    target_dir: Path,
    *,
    handoff_scope: str = "sprint_handoff",
    handoff_sprint_id: str = "sprint-01",
) -> ExecutionRequest:
    return ExecutionRequest(
        run_id=run_dir.name,
        agent_id="fullstack-agent",
        agent_version="0.1.0",
        maturity_level="L6 Codex Agent",
        provider="codex",
        model="gpt-5.3-codex",
        target_project_dir=str(target_dir),
        input_artifacts=["01-intake-brief.json", "04-workflow-plan.json"],
        expected_outputs=[],
        instructions=[],
        constraints=[],
        feature_queue=[{"id": "F1", "title": "Create tasks", "delivery_order": 1}],
        completed_feature_ids=["F1"],
        handoff_scope=handoff_scope,
        handoff_sprint_id=handoff_sprint_id,
        handoff_output_dir=(
            f"handoff/sprints/{handoff_sprint_id}"
            if handoff_scope == "sprint_handoff"
            else "handoff/project/final"
        ),
        handoff_expected_outputs=[
            f"handoff/sprints/{handoff_sprint_id}/09-handoff-summary.md",
            f"handoff/sprints/{handoff_sprint_id}/release-report.html",
            f"handoff/sprints/{handoff_sprint_id}/release-evidence.json",
        ]
        if handoff_scope == "sprint_handoff"
        else [
            "handoff/project/final/09-handoff-summary.md",
            "handoff/project/final/release-report.html",
            "handoff/project/final/release-evidence.json",
        ],
    )
