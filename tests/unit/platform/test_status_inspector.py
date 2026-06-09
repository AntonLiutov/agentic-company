import json
import re
import subprocess
from pathlib import Path

from agentic_company.console.web.db import ConsoleRepository
from agentic_company.platform.status_inspector import (
    StatusInspectionRequest,
    StatusInspectorRunner,
    build_status_inspection_prompt,
)


def test_status_inspector_runner_writes_and_reads_status_json(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        match = re.search(r"Required output JSON: (.+)", prompt)
        assert match
        status_path = match.group(1).strip()
        payload = {
            "status": "inspected",
            "scope": "sprint",
            "sprint_id": "sprint-01",
            "sprint_status": "running",
            "tasks": [
                {
                    "id": "F1",
                    "status": "implemented",
                    "owner_agent": "fullstack-agent",
                    "evidence_refs": ["07-execution-summary-F1.md"],
                    "blockers": [],
                }
            ],
            "workers_called": ["run_fullstack"],
            "gates": {
                "implementation_done": True,
                "qa_passed": False,
                "deployment_done": False,
                "handoff_ready": False,
            },
            "can_complete_sprint": False,
            "status_summary": "F1 needs QA.",
            "status_legend": {"implemented": "Owner work exists but QA is pending."},
        }
        with open(status_path, "w", encoding="utf-8") as file:
            json.dump(payload, file)
        return subprocess.CompletedProcess(command, 0, stdout="inspected", stderr="")

    result = StatusInspectorRunner(command_executor=executor).run(
        StatusInspectionRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="team-lead-agent",
            scope="sprint",
            purpose="Find next sprint action.",
            status_context={"sprint_id": "sprint-01"},
            artifact_refs=["team-lead/sprint-01-history.json"],
            correlation_id="sprint-01",
        )
    )

    assert result.status == "inspected"
    assert "next_action" not in result.payload
    assert result.result_artifact.endswith("status.json")
    assert result.result_artifact.startswith("team-lead/status-inspections/")


def test_status_inspector_runner_reads_and_normalizes_utf8_bom_status_json(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        match = re.search(r"Required output JSON: (.+)", prompt)
        assert match
        status_path = Path(match.group(1).strip())
        payload = {
            "status": "inspected",
            "scope": "delivery",
            "delivery_status": "running",
            "sprints": [],
            "workers_called": ["run_qa"],
            "gates": {
                "planning_done": True,
                "all_sprints_done": False,
                "deployment_done": False,
                "final_handoff_ready": False,
            },
            "can_complete_delivery": False,
            "status_summary": "QA is still in progress.",
            "status_legend": {"running": "Delivery work is still active."},
        }
        status_path.write_text(json.dumps(payload), encoding="utf-8-sig")
        return subprocess.CompletedProcess(command, 0, stdout="inspected", stderr="")

    result = StatusInspectorRunner(command_executor=executor).run(
        StatusInspectionRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="head-agent",
            scope="delivery",
            purpose="Inspect delivery status.",
            status_context={"status": "qa_running"},
            artifact_refs=[],
            correlation_id="delivery",
        )
    )

    status_path = run_dir / result.result_artifact
    assert result.status == "inspected"
    assert result.payload["can_complete_delivery"] is False
    assert not status_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_status_inspector_runner_writes_failed_status_when_json_missing(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _create_run(tmp_path, monkeypatch, run_dir, "run")

    def executor(command, prompt, timeout_seconds, log_path, raw_events_path):
        return subprocess.CompletedProcess(command, 0, stdout="summary only", stderr="")

    result = StatusInspectorRunner(command_executor=executor).run(
        StatusInspectionRequest(
            run_id="run",
            run_dir=run_dir,
            requesting_agent="team-lead-agent",
            scope="sprint",
            purpose="Inspect sprint status.",
            status_context={"sprint_id": "sprint-01"},
            artifact_refs=[],
            correlation_id="sprint-01",
        )
    )

    status_path = run_dir / result.result_artifact
    persisted = json.loads(status_path.read_text(encoding="utf-8"))
    assert result.status == "inspection_failed"
    assert persisted == result.payload
    assert persisted["errors"] == [f"missing_or_invalid_artifact: {result.result_artifact}"]


def test_status_inspection_prompt_requires_json_readback(tmp_path):
    prompt = build_status_inspection_prompt(
        StatusInspectionRequest(
            run_id="run",
            run_dir=tmp_path,
            requesting_agent="head-agent",
            scope="delivery",
            purpose="Inspect delivery status.",
            status_context={"status": "team_lead_sprint_handoff_ready"},
        ),
        context_path=tmp_path / "context.json",
        result_path=tmp_path / "status.json",
    )

    assert "Write exactly one machine-readable status JSON object" in prompt
    assert "UTF-8 without BOM" in prompt
    assert "Set-Content -Encoding UTF8" in prompt
    assert "can_complete_delivery" in prompt
    assert "status_legend" in prompt
    assert "Do not recommend tools, owners, routing, or next actions" in prompt
    assert "next_action" not in prompt
    assert "next_required_owner" not in prompt


def _create_run(tmp_path: Path, monkeypatch, run_dir: Path, run_uid: str) -> None:
    repo = ConsoleRepository()
    repo.init_schema()
    user = repo.create_user(
        email=f"{run_uid}@example.test",
        username=f"user-{run_uid}",
        password="password-1",
    )
    project = repo.create_project(
        owner_user_id=user.id,
        name="Status",
        request_text="Status",
        mode="internal_tool",
        complexity="simple",
        status="running",
    )
    repo.create_run(
        project_id=project.id,
        run_uid=run_uid,
        run_dir=run_dir,
        status="running",
        mode="internal_tool",
        reasoning="medium",
    )
