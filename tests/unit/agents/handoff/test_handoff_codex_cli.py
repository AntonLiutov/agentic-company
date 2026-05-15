import json
import subprocess
from pathlib import Path

from agentic_company.agents.handoff.codex_cli import (
    HANDOFF_EVIDENCE_JSON,
    HANDOFF_REPORT_HTML,
    HANDOFF_SUMMARY_MARKDOWN,
    HandoffCodexRunner,
    build_handoff_codex_prompt,
    read_handoff_contract,
)
from agentic_company.platform.models import ExecutionRequest


def test_handoff_codex_runner_accepts_contract_artifacts(tmp_path):
    run_dir = tmp_path / "runs" / "handoff-codex"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir(parents=True)
    target_dir.mkdir()
    _write_execution_request(run_dir, target_dir)

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        assert "Handoff Codex Agent" in prompt
        assert "platform will not render a predefined report template" in prompt
        assert timeout_seconds == 1800
        (run_dir / HANDOFF_SUMMARY_MARKDOWN).write_text("# Release\n", encoding="utf-8")
        (run_dir / HANDOFF_REPORT_HTML).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / HANDOFF_REPORT_HTML).write_text("<html>ready</html>\n", encoding="utf-8")
        (run_dir / HANDOFF_EVIDENCE_JSON).write_text(
            json.dumps({"status": "ready"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="HANDOFF_STATUS: ready\n", stderr="")

    result = HandoffCodexRunner(command_executor=executor).run(run_dir)

    assert result.agent_id == "handoff-codex-agent"
    assert result.status == "handoff_ready"
    assert HANDOFF_SUMMARY_MARKDOWN in result.output_artifacts
    assert HANDOFF_REPORT_HTML in result.output_artifacts
    assert HANDOFF_EVIDENCE_JSON in result.output_artifacts


def test_handoff_codex_runner_recovers_contract_from_generated_project(tmp_path):
    run_dir = tmp_path / "runs" / "handoff-fallback"
    target_dir = run_dir / "generated-project"
    run_dir.mkdir(parents=True)
    target_dir.mkdir()
    _write_execution_request(run_dir, target_dir)

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        fallback = target_dir / "handoff"
        fallback.mkdir(parents=True)
        (fallback / HANDOFF_SUMMARY_MARKDOWN).write_text("# Release\n", encoding="utf-8")
        (fallback / "release-report.html").write_text("<html>ready</html>\n", encoding="utf-8")
        (fallback / "release-evidence.json").write_text(
            json.dumps({"status": "ready"}) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="HANDOFF_STATUS: ready\n", stderr="")

    result = HandoffCodexRunner(command_executor=executor).run(run_dir)

    assert result.status == "handoff_ready"
    assert (run_dir / HANDOFF_SUMMARY_MARKDOWN).exists()
    assert (run_dir / HANDOFF_REPORT_HTML).exists()
    assert (run_dir / HANDOFF_EVIDENCE_JSON).exists()


def test_handoff_codex_prompt_is_agent_owned_and_non_exhaustive(tmp_path):
    run_dir = tmp_path / "run"
    target_dir = run_dir / "generated-project"
    request = _execution_request(run_dir, target_dir)

    prompt = build_handoff_codex_prompt(request, run_dir, attempt=1, previous_summary="")

    assert "sole owner of client-facing release communication" in prompt
    assert "will not render a predefined report template" in prompt
    assert "client-facing release communication" in prompt
    assert "client sponsor, product owner" in prompt
    assert "They want to know what is ready, where to click" in prompt
    assert "Write directly to the client" in prompt
    assert "simple, clear, complete, and non-repetitive" in prompt
    assert "stakeholder review" in prompt
    assert "The HTML must stand alone" in prompt
    assert "show only links a business stakeholder" in prompt
    assert "Technical integration" in prompt
    assert "network/search tools are available" in prompt
    assert str(run_dir / HANDOFF_SUMMARY_MARKDOWN) in prompt
    assert str(run_dir / HANDOFF_REPORT_HTML) in prompt
    assert str(run_dir / HANDOFF_EVIDENCE_JSON) in prompt


def test_handoff_contract_rejects_missing_status_line(tmp_path):
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

    assert not contract["contract_valid"]
    assert "HANDOFF_STATUS" in contract["contract_errors"][0]


def _write_execution_request(run_dir: Path, target_dir: Path) -> None:
    (run_dir / "06-execution-request.json").write_text(
        json.dumps(_execution_request(run_dir, target_dir).to_dict()),
        encoding="utf-8",
    )


def _execution_request(run_dir: Path, target_dir: Path) -> ExecutionRequest:
    return ExecutionRequest(
        run_id=run_dir.name,
        agent_id="fullstack-agent",
        agent_version="0.1.0",
        maturity_level="L6 Codex Agent",
        provider="codex",
        model="gpt-5.5",
        target_project_dir=str(target_dir),
        input_artifacts=["01-intake-brief.json", "04-workflow-plan.json"],
        expected_outputs=[],
        instructions=[],
        constraints=[],
        project_archetype="api-web-compose",
        feature_queue=[{"id": "F1", "title": "Create tasks", "delivery_order": 1}],
        completed_feature_ids=["F1"],
    )
